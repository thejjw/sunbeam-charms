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

"""Unit tests for Barbican operator."""

import charm
import ops_sunbeam.test_utils as test_utils


class _BarbicanTestOperatorCharm(charm.BarbicanOperatorCharm):
    """Test Operator Charm for Barbican Operator."""

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
        return "barbican.juju"


class TestBarbicanOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Barbican Operator."""

    PATCHES = []

    def setUp(self):
        """Set up environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _BarbicanTestOperatorCharm, container_calls=self.container_calls
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
        self.assertEqual(len(self.harness.charm.seen_events), 2)

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)

        setup_cmds = [
            ["a2ensite", "wsgi-barbican-api"],
            ["sudo", "-u", "barbican", "barbican-manage", "db", "upgrade"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["barbican-api"])
        config_files = [
            "/etc/apache2/sites-available/wsgi-barbican-api.conf",
            "/etc/barbican/barbican.conf",
        ]
        for f in config_files:
            self.check_file("barbican-api", f)


def add_db_relation(harness, name) -> str:
    """Add db relation."""
    rel_id = harness.add_relation(name, "mysql")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.update_relation_data(
        rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
    )
    return rel_id
