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

"""Unit tests for the Ironic K8s charm."""

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)


class _IronicOperatorCharm(charm.IronicOperatorCharm):
    """Test implementation of Ironic operator."""

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

    @property
    def public_ingress_address(self):
        return "ironic.juju"


class TestIronicOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Ironic Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _IronicOperatorCharm, container_calls=self.container_calls
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

    def _check_file_contents(self, container, path, strings):
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore

        with client.pull(path) as infile:
            received_data = infile.read()

        for string in strings:
            self.assertIn(string, received_data)

    def add_ironic_api_relation(self):
        """Add ironic-api relation."""
        return self.harness.add_relation(charm.IRONIC_API_PROVIDES, "consumer")

    def add_db_relation(self, harness: Harness, name: str) -> str:
        """Add db relation."""
        rel_id = harness.add_relation(name, "mysql")
        harness.add_relation_unit(rel_id, "mysql/0")
        harness.update_relation_data(
            rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
        )
        return rel_id

    def add_complete_ingress_relation(self) -> None:
        """Add complete ingress and traefik-route relations."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.add_relation(
            "traefik-route-public",
            "traefik",
            app_data={"external_host": "dummy-ip", "scheme": "http"},
        )
        self.harness.add_relation(
            "traefik-route-internal",
            "ironic",
            app_data={"external_host": "dummy-ip", "scheme": "http"},
        )

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 2)

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_ironic_api_relation()

        ironic_api_rel = self.harness.model.get_relation("ironic-api")
        rel_data = ironic_api_rel.data[self.harness.model.app]
        self.assertEqual({}, rel_data)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        self.add_complete_ingress_relation()

        setup_cmds = [
            ["a2ensite", "wsgi-ironic-api"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["ironic-api"])

        config_files = [
            "/etc/apache2/sites-available/wsgi-ironic-api.conf",
            "/etc/ironic/api_audit_map.conf",
            "/etc/ironic/ironic.conf",
            "/etc/ironic/rootwrap.conf",
        ]
        for f in config_files:
            self.check_file("ironic-api", f)

        self._check_file_contents(
            "ironic-api",
            "/etc/ironic/ironic.conf",
            ["public_endpoint = http://public-url:80"],
        )

        config_files = [
            "/etc/ironic/ironic.conf",
            "/etc/ironic/rootwrap.conf",
        ]
        for f in config_files:
            self.check_file("ironic-novncproxy", f)

        self.assertEqual({"ready": "true"}, rel_data)
