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

"""Unit tests for the Manila K8s charm."""

import charm
import charms.manila_k8s.v0.manila as manila_k8s
import ops_sunbeam.test_utils as test_utils
from ops import (
    model,
)
from ops.testing import (
    Harness,
)


class _ManilaOperatorCharm(charm.ManilaOperatorCharm):
    """Test implementation of Manila operator."""

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
        return "manila.juju"


class TestManilaOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Manila Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _ManilaOperatorCharm, container_calls=self.container_calls
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

    def add_manila_relation(self) -> int:
        """Add the manila relation and unit data."""
        return self.harness.add_relation(
            "manila",
            "manila-cephfs",
            app_data={manila_k8s.SHARE_PROTOCOL: "foo"},
        )

    def _check_file_contents(self, container, path, strings):
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore

        with client.pull(path) as infile:
            received_data = infile.read()

        for string in strings:
            self.assertIn(string, received_data)

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
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

        # manila is a required relation.
        manila_share_status = self.harness.charm.manila_share.status
        self.assertIsInstance(manila_share_status.status, model.BlockedStatus)

        # Add the manila relation.
        manila_rel_id = self.add_manila_relation()

        setup_cmds = [
            ["a2ensite", "wsgi-manila-api"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["manila-api"])

        setup_cmds = [
            ["sudo", "-u", "manila", "manila-manage", "db", "sync"],
        ]
        for cmd in setup_cmds:
            self.assertIn(
                cmd, self.container_calls.execute["manila-scheduler"]
            )

        config_files = [
            "/etc/apache2/sites-available/wsgi-manila-api.conf",
            "/etc/manila/api-paste.ini",
            "/etc/manila/manila.conf",
        ]
        for f in config_files:
            self.check_file("manila-api", f)

        config_files = [
            "/etc/manila/manila.conf",
            "/usr/local/share/ca-certificates/ca-bundle.pem",
        ]
        for f in config_files:
            self.check_file("manila-scheduler", f)

        for container_name in ["manila-api", "manila-scheduler"]:
            self._check_file_contents(
                container_name,
                "/etc/manila/manila.conf",
                ["enabled_share_protocols = foo"],
            )

        self.harness.remove_relation(manila_rel_id)

        self.assertIsInstance(manila_share_status.status, model.BlockedStatus)
