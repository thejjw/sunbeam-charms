#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd.
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

"""Tests for neutron charm."""

import mock
import ops_sunbeam.test_utils as test_utils

import charm


class _NeutronOVNOperatorCharm(charm.NeutronOVNOperatorCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        """Log events."""
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        """Log configure charm call."""
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        """Ingress address for charm."""
        return "neutron.juju"


class TestNeutronOperatorCharm(test_utils.CharmTestCase):
    """Classes for testing neutron charms."""

    PATCHES = []

    @mock.patch(
        "charms.observability_libs.v0.kubernetes_service_patch."
        "KubernetesServicePatch"
    )
    def setUp(self, mock_patch):
        """Setup Neutron tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _NeutronOVNOperatorCharm, container_calls=self.container_calls
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
        """Test Pebble ready event is captured."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("neutron-server")
        self.assertEqual(len(self.harness.charm.seen_events), 1)

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)

        setup_cmds = [
            [
                "sudo",
                "-u",
                "neutron",
                "neutron-db-manage",
                "--config-file",
                "/etc/neutron/neutron.conf",
                "--config-file",
                "/etc/neutron/plugins/ml2/ml2_conf.ini",
                "upgrade",
                "head",
            ]
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["neutron-server"])

        config_files = [
            "/etc/neutron/neutron.conf",
            "/etc/neutron/api-paste.ini",
            "/etc/neutron/plugins/ml2/cert_host",
            "/etc/neutron/plugins/ml2/key_host",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            "/etc/neutron/plugins/ml2/neutron-ovn.crt",
        ]

        for f in config_files:
            self.check_file("neutron-server", f)
