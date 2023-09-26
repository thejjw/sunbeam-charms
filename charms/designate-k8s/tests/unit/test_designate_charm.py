#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for Designate operator."""

import json

import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)

import charm


class _DesignateTestOperatorCharm(charm.DesignateOperatorCharm):
    """Test Operator Charm for Designate Operator."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        return "designate.juju"


class TestDesignateOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Designate Operator."""

    PATCHES = []

    def setUp(self):
        """Set up environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _DesignateTestOperatorCharm, container_calls=self.container_calls
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.database_requires import (
            DatabaseEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 1)

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.charm.on.install.emit()
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)
        rel_id = add_base_rndc_relation(self.harness)
        add_rndc_relation_credentials(self.harness, rel_id)

        setup_cmds = [
            ["a2ensite", "wsgi-designate-api"],
            [
                "sudo",
                "-u",
                "designate",
                "designate-manage",
                "database",
                "sync",
            ],
            [
                "sudo",
                "-u",
                "designate",
                "designate-manage",
                "pool",
                "update",
            ],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["designate"])
        config_files = [
            "/etc/apache2/sites-available/wsgi-designate-api.conf",
            "/etc/designate/designate.conf",
            "/etc/designate/pools.yaml",
            "/etc/designate/rndc.key",
        ]
        for f in config_files:
            self.check_file("designate", f)


def add_base_rndc_relation(harness: Harness) -> int:
    """Add amqp relation."""
    rel_id = harness.add_relation("dns-backend", "bind9")
    harness.add_relation_unit(rel_id, "bind9/0")
    return rel_id


def add_rndc_relation_credentials(harness: Harness, rel_id: int) -> None:
    """Add amqp data to amqp relation."""
    secret = harness.add_model_secret("bind9", {"secret": "rndc_secret"})
    harness.grant_secret(secret, "designate-k8s")
    nonce = harness.get_relation_data(rel_id, "designate-k8s/0").get("nonce")
    harness.update_relation_data(
        rel_id,
        "bind9",
        {
            "host": "10.20.20.20",
            "rndc_keys": json.dumps(
                {nonce: {"algorithm": "hmac-256", "secret": secret}}
            ),
        },
    )
