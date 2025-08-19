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

"""Unit tests for Magnum operator."""

import json
from unittest.mock import (
    Mock,
)

import charm
import ops_sunbeam.test_utils as test_utils
import yaml
from ops.testing import (
    Harness,
)


class _MagnumTestOperatorCharm(charm.MagnumOperatorCharm):
    """Test Operator Charm for Magnum Operator."""

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
        return "magnum.juju"


class TestMagnumOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Magnum Operator."""

    PATCHES = []

    def setUp(self):
        """Set up environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _MagnumTestOperatorCharm, container_calls=self.container_calls
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequiresEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

        # Create a secret for kubeconfig and update the charm config
        secret_id = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"kubeconfig": yaml.dump({"cluster": "testcluster"})},
        )
        self.harness.update_config({"kubeconfig": secret_id})

    def add_complete_identity_resource_relation(self, harness: Harness) -> int:
        """Add complete Identity resource relation."""
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.user_id_ops.get_config_credentials = Mock(
            return_value=("test", "test")
        )
        harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "response": json.dumps(
                    {
                        "id": 1,
                        "tag": "initial_magnum_domain_setup",
                        "ops": [{"name": "create_domain", "return-code": 0}],
                    }
                )
            },
        )
        return rel_id

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(
            self.harness.charm.seen_events, ["ConfigChangedEvent"]
        )
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 3)

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)
        self.add_complete_identity_resource_relation(self.harness)

        setup_cmds = [
            ["sudo", "-u", "magnum", "magnum-db-manage", "upgrade"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["magnum-api"])
        config_files = [
            "/etc/apache2/sites-available/wsgi-magnum-api.conf",
            "/etc/magnum/magnum.conf",
        ]
        for f in config_files:
            self.check_file("magnum-api", f)
