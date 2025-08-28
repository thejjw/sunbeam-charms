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

"""Unit tests for Cloudkitty operator."""

import charm
import ops_sunbeam.test_utils as test_utils


class _CloudkittyTestOperatorCharm(charm.CloudkittyOperatorCharm):
    """Test Operator Charm for Cloudkitty Operator."""

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
        return "Cloudkitty.juju"


class TestCloudkittyOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Cloudkitty Operator."""

    PATCHES = []

    def setUp(self):
        """Set up environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _CloudkittyTestOperatorCharm, container_calls=self.container_calls
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
        # self.harness.begin_with_initial_hooks()

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 2)

    def test_all_relations(self):
        """Test all the charms relations."""
        # self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)
        # test_utils.add_complete_amqp_relation(self.harness)
        self.harness.add_relation(
            "gnocchi-db", "gnocchi", app_data={"ready": "true"}
        )

        setup_cmds = [
            ["cloudkitty-dbsync", "upgrade"],
            ["cloudkitty-storage-init"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["cloudkitty"])

        config_files = [
            "/etc/apache2/sites-available/wsgi-cloudkitty-api.conf",
            "/etc/cloudkitty/cloudkitty.conf",
        ]
        for f in config_files:
            self.check_file("cloudkitty", f)

def add_db_relation(harness, name) -> str:
    """Add db relation."""
    rel_id = harness.add_relation(name, "mysql")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.update_relation_data(
        rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
    )
    return rel_id
