#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
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

"""Unit tests for the Ironic Conductor K8s charm."""

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)


class _IronicConductorOperatorCharm(charm.IronicConductorOperatorCharm):
    """Test implementation of Ironic Conductor operator."""

    def __init__(self, framework):
        self.seen_events = []
        self.render_calls = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def renderer(self, containers, container_configs, template_dir, adapters):
        """Intercept and record all calls to render config files."""
        self.render_calls.append(
            (containers, container_configs, template_dir, adapters)
        )

    def configure_charm(self, event):
        """Intercept and record full charm configuration events."""
        super().configure_charm(event)
        self._log_event(event)


class TestIronicConductorOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Ironic Conductor Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _IronicConductorOperatorCharm, container_calls=self.container_calls
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

    def add_db_relation(self, harness: Harness, name: str) -> str:
        """Add db relation."""
        rel_id = harness.add_relation(name, "mysql")
        harness.add_relation_unit(rel_id, "mysql/0")
        harness.update_relation_data(
            rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
        )
        return rel_id

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("ironic-conductor")
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)

        config_files = [
            "/etc/ironic/ironic.conf",
            "/etc/ironic/rootwrap.conf",
            "/tftpboot/map-file",
            "/tftpboot/grub/grub.cfg",
        ]
        for f in config_files:
            self.check_file("ironic-conductor", f)
