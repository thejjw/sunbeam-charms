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

import charm
import charms.neutron_k8s.v0.switch_config as switch_config
import ops_sunbeam.test_utils as test_utils

_BAREMETAL_SAMPLE_CONFIG = """["%(name)s.example.net"]
driver = "netconf-openconfig"
device_params = "name:%(name)s"
switch_info = "%(name)s"
switch_id = "00:53:00:0a:0a:0a"
host = "%(name)s.example.net"
username = "user"
key_filename = "/etc/neutron/ssh_keys/%(name)s_sshkey"
"""


def _get_baremetal_sample_config(name: str) -> str:
    return _BAREMETAL_SAMPLE_CONFIG % {"name": name}


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

    def setUp(self):
        """Setup Neutron tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _NeutronOVNOperatorCharm, container_calls=self.container_calls
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

    def add_ironic_api_relation(self) -> None:
        """Add ironic-api relation."""
        return self.harness.add_relation(
            "ironic-api",
            "ironic",
            app_data={"ready": "true"},
        )

    def add_baremetal_config_relation(self) -> None:
        """Adds baremetal-switch-config relation."""
        secrets = []
        for name in ["nexus", "suxen"]:
            secret_data = {
                "conf": _get_baremetal_sample_config(name),
                f"{name}-sshkey": "foo",
            }
            # neutron-baremetal-switch-config-k8s is meant to grant these secrets to the
            # neutron-k8s charm. We'll closely emulate this here.
            secret_id = self.harness.add_model_secret(
                "neutron-baremetal-config",
                secret_data,
            )
            secrets.append(secret_id)

        app_data = {
            switch_config.SWITCH_CONFIG: ",".join(secrets),
        }
        rel_id = self.harness.add_relation(
            "baremetal-switch-config",
            "neutron-baremetal-config",
        )

        # grant secrets to neutron-k8s charm.
        for secret_id in secrets:
            self.harness.grant_secret(secret_id, self.harness.charm.app.name)

        self.harness.update_relation_data(
            rel_id,
            "neutron-baremetal-config",
            app_data,
        )

        return rel_id

    def _check_file_contents(self, container, path, strings):
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore

        with client.pull(path) as infile:
            received_data = infile.read()

        for string in strings:
            self.assertIn(string, received_data)

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
        self.add_baremetal_config_relation()

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
            charm.IRONIC_AGENT_CONF,
        ]

        for f in config_files:
            self.check_file("neutron-server", f)

        self.assertTrue(
            all([h.service_ready for h in self.harness.charm.pebble_handlers])
        )

        container = self.harness.charm.unit.get_container("neutron-server")

        plan = container.get_plan()
        svc = plan.services["neutron-server"]
        expected_cmd = [
            "neutron-server",
            "--config-dir",
            "/etc/neutron",
            "--config-file",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            "--config-file",
            charm.ML2_BAREMETAL_CONF,
        ]
        self.assertEqual(svc.command, " ".join(expected_cmd))

        # ironic-api relation is not added yet, the ironic-neutron-agent should
        # not be running.
        svc = container.get_service(charm.IRONIC_AGENT)
        self.assertFalse(svc.is_running())
        self._check_file_contents(
            "neutron-server",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            ["mechanism_drivers = sriovnicswitch,ovn"],
        )

        # Add the ironic-api relation, the pebble plan should have the
        # ironic-neutron-agent service should be running.
        rel_id = self.add_ironic_api_relation()

        self.assertTrue(
            all([h.service_ready for h in self.harness.charm.pebble_handlers])
        )

        svc = container.get_service(charm.IRONIC_AGENT)
        self.assertTrue(svc.is_running())
        self._check_file_contents(
            "neutron-server",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            ["mechanism_drivers = baremetal,sriovnicswitch,ovn"],
        )

        # Remove ironic-api relation, check that the ironic-neutron-agent service
        # startup is set to disabled.
        self.harness.remove_relation(rel_id)

        self.assertTrue(
            all([h.service_ready for h in self.harness.charm.pebble_handlers])
        )

        svc = container.get_service(charm.IRONIC_AGENT)
        self.assertFalse(svc.is_running())
        self._check_file_contents(
            "neutron-server",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            ["mechanism_drivers = sriovnicswitch,ovn"],
        )

    def test_baremetal_switch_config_relation(self):
        """Test the baremetal_switch_config relation."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)
        rel_id = self.add_baremetal_config_relation()

        container = self.harness.charm.unit.get_container("neutron-server")
        plan = container.get_plan()
        svc = plan.services["neutron-server"]
        expected_cmd = [
            "neutron-server",
            "--config-dir",
            "/etc/neutron",
            "--config-file",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            "--config-file",
            charm.ML2_BAREMETAL_CONF,
        ]
        self.assertEqual(svc.command, " ".join(expected_cmd))

        config_files = [
            "/etc/neutron/ssh_keys/nexus_sshkey",
            "/etc/neutron/ssh_keys/suxen_sshkey",
        ]

        for f in config_files:
            self._check_file_contents("neutron-server", f, ["foo"])

        contents = [
            "[networking_baremetal]",
            "enabled_devices = nexus.example.net,suxen.example.net",
        ]

        self._check_file_contents(
            "neutron-server",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            contents,
        )

        contents = [
            _get_baremetal_sample_config("nexus"),
            _get_baremetal_sample_config("suxen"),
        ]

        self._check_file_contents(
            "neutron-server",
            charm.ML2_BAREMETAL_CONF,
            contents,
        )

        # Remove the relation.
        self.harness.remove_relation(rel_id)

        plan = container.get_plan()
        svc = plan.services["neutron-server"]
        expected_cmd = [
            "neutron-server",
            "--config-dir",
            "/etc/neutron",
            "--config-file",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
        ]
        self.assertEqual(svc.command, " ".join(expected_cmd))
