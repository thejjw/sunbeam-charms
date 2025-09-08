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

"""Tests for Openstack hypervisor charm."""

import base64
import contextlib
import json
import os
import tempfile
from unittest import (
    mock,
)
from unittest.mock import (
    MagicMock,
)

import charm
import charms.operator_libs_linux.v2.snap as snap
import jsonschema
import ops
import ops.testing
import ops_sunbeam.test_utils as test_utils


class _HypervisorOperatorCharm(charm.HypervisorOperatorCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    """Test charm to test relations."""

    PATCHES = [
        "socket",
        "snap",
        "get_local_ip_by_default_route",
        "subprocess",
        "epa_client",
    ]

    def setUp(self):
        """Setup OpenStack Hypervisor tests."""
        super().setUp(charm, self.PATCHES)
        self.patch_obj(os, "system")

        self.snap.SnapError = Exception
        self.harness = test_utils.get_harness(
            _HypervisorOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Setting up relations."""
        self.harness.update_config({"snap-channel": "essex/stable"})
        self.harness.begin_with_initial_hooks()
        test_utils.add_complete_certificates_relation(self.harness)
        self.harness.add_relation(
            "ovsdb-cms",
            "ovn-relay",
            app_data={"loadbalancer-address": "10.15.24.37"},
            unit_data={
                "bound-address": "10.1.176.143",
                "bound-hostname": "ovn-relay-0.ovn-relay-endpoints.openstack.svc.cluster.local",
                "egress-subnets": "10.20.21.10/32",
                "ingress-address": "10.20.21.10",
                "ingress-bound-address": "10.20.21.10",
                "private-address": "10.20.21.10",
            },
        )

        ceph_rel_id = self.harness.add_relation("ceph-access", "cinder-ceph")
        self.harness.add_relation_unit(ceph_rel_id, "cinder-ceph/0")

        credentials_content = {"uuid": "ddd", "key": "eee"}
        credentials_id = self.harness.add_model_secret(
            "cinder-ceph", credentials_content
        )

        self.harness.grant_secret(credentials_id, self.harness.charm.app.name)
        self.harness.update_relation_data(
            ceph_rel_id,
            "cinder-ceph",
            {"access-credentials": credentials_id},
        )

    def test_mandatory_relations(self):
        """Test all the charms relations."""
        self.get_local_ip_by_default_route.return_value = "10.0.0.10"
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        self.socket.getfqdn.return_value = "test.local"
        self.socket.gethostname.return_value = "test"
        self.initial_setup()
        self.harness.set_leader()

        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        # Add nova-service relation
        self.harness.add_relation(
            "nova-service",
            "nova",
            app_data={
                "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html"
            },
        )

        hypervisor_snap_mock.ensure.assert_any_call(
            "latest", channel="essex/stable"
        )
        metadata = self.harness.charm.metadata_secret()
        cacert = test_utils.TEST_CA
        cacert_with_intermediates = (
            test_utils.TEST_CA + "\n" + "\n".join(test_utils.TEST_CHAIN)
        )
        cacert = base64.b64encode(cacert.encode()).decode()
        cacert_with_intermediates = base64.b64encode(
            cacert_with_intermediates.encode()
        ).decode()
        private_key = base64.b64encode(
            self.harness.charm.contexts().certificates.key.encode()
        ).decode()
        certificate = base64.b64encode(
            test_utils.TEST_SERVER_CERT.encode()
        ).decode()
        expect_settings = {
            "compute.cpu-mode": "host-model",
            "compute.spice-proxy-address": "10.0.0.10",
            "compute.cacert": cacert,
            "compute.cert": certificate,
            "compute.key": private_key,
            "compute.migration-address": "10.0.0.10",
            "compute.pci-device-specs": None,
            "compute.resume-on-boot": True,
            "compute.rbd-user": "nova",
            "compute.rbd-secret-uuid": "ddd",
            "compute.rbd-key": "eee",
            "compute.spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html",
            "credentials.ovn-metadata-proxy-shared-secret": metadata,
            "identity.admin-role": None,
            "identity.auth-url": "http://10.153.2.45:80/openstack-keystone",
            "identity.password": "user-password",
            "identity.project-domain-id": "pdomain-id",
            "identity.project-domain-name": "pdomain_-ame",
            "identity.project-id": "uproj-id",
            "identity.project-name": "user-project",
            "identity.region-name": "region12",
            "identity.user-domain-id": "udomain-id",
            "identity.user-domain-name": "udomain-name",
            "identity.username": "username",
            "logging.debug": False,
            "monitoring.enable": False,
            "network.dns-servers": "8.8.8.8",
            "network.external-bridge": "br-ex",
            "network.external-bridge-address": "10.20.20.1/24",
            "network.ip-address": "10.0.0.10",
            "network.ovn-cacert": cacert_with_intermediates,
            "network.ovn-cert": certificate,
            "network.ovn-key": private_key,
            "network.ovn-sb-connection": "ssl:10.15.24.37:6642",
            "network.physnet-name": "physnet1",
            "network.ovs-dpdk-enabled": False,
            "node.fqdn": "test.local",
            "node.ip-address": "10.0.0.10",
            "rabbitmq.url": "rabbit://hypervisor:rabbit.pass@rabbithost1.local:5672/openstack",
            "telemetry.enable": False,
            "ca.bundle": None,
            "masakari.enable": False,
            "sev.reserved-host-memory-mb": None,
        }
        hypervisor_snap_mock.set.assert_any_call(expect_settings, typed=True)

    def test_all_relations(self):
        """Test all the charms relations."""
        # Add cos-agent relation
        self.harness.add_relation(
            "cos-agent",
            "grafana-agent",
            unit_data={
                "config": '{"metrics_alert_rules": {}, "log_alert_rules": {}, "dashboards": ["/Td6WFoAAATm1rRGAAAAABzfRCEftvN9AQAAAAAEWVo="], "metrics_scrape_jobs": [{"metrics_path": "/metrics", "static_configs": [{"targets": ["localhost:9177"]}]], "log_slots": []}',
                "egress-subnets": "10.1.171.64/32",
                "ingress-address": "10.1.171.64",
                "private-address": "10.1.171.64",
            },
        )

        # Add ceilometer-service relation
        self.harness.add_relation(
            "ceilometer-service",
            "ceilometer",
            app_data={"telemetry-secret": "FAKE_SECRET"},
        )

        # Add nova-service relation
        self.harness.add_relation(
            "nova-service",
            "nova",
            app_data={
                "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spiceauto.html"
            },
        )

        # Add masakari-service relation
        self.harness.add_relation(
            "masakari-service",
            "masakari",
            app_data={"ready": "true"},
        )

        self.get_local_ip_by_default_route.return_value = "10.0.0.10"
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        self.socket.getfqdn.return_value = "test.local"
        self.socket.gethostname.return_value = "test"
        self.initial_setup()
        self.harness.set_leader()
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        hypervisor_snap_mock.ensure.assert_any_call(
            "latest", channel="essex/stable"
        )
        metadata = self.harness.charm.metadata_secret()
        cacert = test_utils.TEST_CA
        cacert_with_intermediates = (
            test_utils.TEST_CA + "\n" + "\n".join(test_utils.TEST_CHAIN)
        )
        cacert = base64.b64encode(cacert.encode()).decode()
        cacert_with_intermediates = base64.b64encode(
            cacert_with_intermediates.encode()
        ).decode()
        private_key = base64.b64encode(
            self.harness.charm.contexts().certificates.key.encode()
        ).decode()
        certificate = base64.b64encode(
            test_utils.TEST_SERVER_CERT.encode()
        ).decode()
        expect_settings = {
            "compute.cpu-mode": "host-model",
            "compute.spice-proxy-address": "10.0.0.10",
            "compute.cacert": cacert,
            "compute.cert": certificate,
            "compute.key": private_key,
            "compute.migration-address": "10.0.0.10",
            "compute.resume-on-boot": True,
            "compute.pci-device-specs": None,
            "compute.rbd-user": "nova",
            "compute.rbd-secret-uuid": "ddd",
            "compute.rbd-key": "eee",
            "compute.spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spiceauto.html",
            "credentials.ovn-metadata-proxy-shared-secret": metadata,
            "identity.admin-role": None,
            "identity.auth-url": "http://10.153.2.45:80/openstack-keystone",
            "identity.password": "user-password",
            "identity.project-domain-id": "pdomain-id",
            "identity.project-domain-name": "pdomain_-ame",
            "identity.project-id": "uproj-id",
            "identity.project-name": "user-project",
            "identity.region-name": "region12",
            "identity.user-domain-id": "udomain-id",
            "identity.user-domain-name": "udomain-name",
            "identity.username": "username",
            "logging.debug": False,
            "monitoring.enable": True,
            "network.dns-servers": "8.8.8.8",
            "network.external-bridge": "br-ex",
            "network.external-bridge-address": "10.20.20.1/24",
            "network.ip-address": "10.0.0.10",
            "network.ovn-cacert": cacert_with_intermediates,
            "network.ovn-cert": certificate,
            "network.ovn-key": private_key,
            "network.ovn-sb-connection": "ssl:10.15.24.37:6642",
            "network.physnet-name": "physnet1",
            "network.ovs-dpdk-enabled": False,
            "node.fqdn": "test.local",
            "node.ip-address": "10.0.0.10",
            "rabbitmq.url": "rabbit://hypervisor:rabbit.pass@rabbithost1.local:5672/openstack",
            "telemetry.enable": True,
            "telemetry.publisher-secret": "FAKE_SECRET",
            "ca.bundle": None,
            "masakari.enable": True,
            "sev.reserved-host-memory-mb": None,
        }
        hypervisor_snap_mock.set.assert_any_call(expect_settings, typed=True)

    def test_openstack_hypervisor_snap_not_installed(self):
        """Check action raises SnapNotFoundError if openstack-hypervisor snap is not installed."""
        self.harness.begin()
        self.snap.SnapCache.side_effect = snap.SnapNotFoundError
        with self.assertRaises(snap.SnapNotFoundError):
            self.harness.run_action("list-nics")

    def test_list_nics_snap_not_installed(self):
        """Check action raises ActionFailed if snap is not installed."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        with self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("list-nics")

    def test_list_nics(self):
        """Check action returns nics."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        subprocess_run_mock = MagicMock()
        subprocess_run_mock.return_value = MagicMock(
            stdout=bytes(
                json.dumps({"nics": ["eth0", "eth1"], "candidates": ["eth2"]}),
                "utf-8",
            ),
            stderr=b"yes things went well",
            returncode=0,
        )
        self.subprocess.run = subprocess_run_mock
        action_output = self.harness.run_action("list-nics")
        assert "candidates" in action_output.results["result"]

    def test_list_nics_error(self):
        """Check action raises ActionFailed if subprocess returns non-zero."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        subprocess_run_mock = MagicMock()
        subprocess_run_mock.return_value = MagicMock(
            stdout=b"",
            stderr=b"things did not go well",
            returncode=1,
        )
        self.subprocess.run = subprocess_run_mock
        with self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("list-nics")

    def test_list_flavors(self):
        """Check action return flavors."""
        flavors = "flavor1,flavor2"
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = flavors
        action_output = self.harness.run_action("list-flavors")
        assert action_output.results["result"] == flavors

    def test_snap_connect_success(self):
        """Test successful snap connect to epa-orchestrator."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.return_value = None

        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        self.harness.charm._connect_to_epa_orchestrator()
        hypervisor_snap_mock.connect.assert_called_once_with(
            charm.EPA_INFO_PLUG, slot=charm.EPA_INFO_SLOT
        )

    def test_snap_connect_failure_snaperror(self):
        """Test snap connect failure with SnapError."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.side_effect = snap.SnapError(
            "Connection failed"
        )

        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        with self.assertRaises(snap.SnapError):
            self.harness.charm._connect_to_epa_orchestrator()

    @contextlib.contextmanager
    def _mock_dpdk_settings_file(self, dpdk_yaml):
        with tempfile.NamedTemporaryFile(mode="w") as f:
            f.write(dpdk_yaml)
            f.flush()
            with mock.patch.object(charm, "DPDK_CONFIG_OVERRIDE_PATH", f.name):
                yield

    def test_get_dpdk_settings_override(self):
        """Ensure DPDK settings override."""
        dpdk_yaml = """
dpdk:
    dpdk-enabled: true
    dpdk-memory: 2048
    dpdk-datapath-cores: 4
    dpdk-controlplane-cores: 4
"""
        exp_result = {
            "dpdk-enabled": True,
            "dpdk-memory": 2048,
            "dpdk-datapath-cores": 4,
            "dpdk-controlplane-cores": 4,
        }
        with self._mock_dpdk_settings_file(dpdk_yaml):
            self.harness.begin()
            result = self.harness.charm._get_dpdk_settings_override()

            self.assertEqual(exp_result, result)

    def test_get_dpdk_settings_override_invalid(self):
        """Ensure that invalid dpdk.yaml files are rejected."""
        dpdk_yaml = """
network:
    dpdk-enabled: true
    ovs-memory: 1
"""
        with self._mock_dpdk_settings_file(dpdk_yaml):
            self.harness.begin()
            self.assertRaises(
                jsonschema.exceptions.ValidationError,
                self.harness.charm._get_dpdk_settings_override,
            )

    def test_core_list_to_bitmask(self):
        """Test converting cpu core lists to bit masks."""
        self.harness.begin()

        self.assertEqual("0x1", self.harness.charm._core_list_to_bitmask([0]))
        self.assertEqual(
            "0xf0", self.harness.charm._core_list_to_bitmask([4, 5, 6, 7])
        )
        self.assertEqual(
            "0x1010101",
            self.harness.charm._core_list_to_bitmask([16, 24, 0, 8]),
        )

        self.assertRaises(
            ValueError, self.harness.charm._core_list_to_bitmask, [0, -1]
        )
        self.assertRaises(
            ValueError, self.harness.charm._core_list_to_bitmask, [2, 2049]
        )

    def test_bitmask_to_core_list(self):
        """Test converting cpu bit masks to core lists."""
        self.harness.begin()

        self.assertEqual([], self.harness.charm._bitmask_to_core_list(0))
        self.assertEqual([0], self.harness.charm._bitmask_to_core_list(1))
        self.assertEqual(
            [4, 5, 6, 7],
            self.harness.charm._bitmask_to_core_list(0xF0),
        )
        self.assertEqual(
            [0, 8, 16, 24],
            self.harness.charm._bitmask_to_core_list(0x1010101),
        )

    @mock.patch("utils.get_pci_numa_node")
    def test_get_dpdk_numa_nodes(self, mock_get_pci_numa_node):
        """Test retrieving numa nodes based on DPDK ports."""
        self.harness.begin()

        dpdk_numa_nodes = {
            "0000:1a:00.0": 0,
            "0000:1a:00.1": 0,
            "0000:ff:00.1": 1,
        }
        dpdk_port_mappings = {
            "ports": {
                "eno3": {
                    "pci_address": "0000:1a:00.0",
                    "mtu": 1500,
                    "bridge": None,
                    "bond": "bond0",
                    "dpdk_port_name": "dpdk-eno3",
                },
                "eno4": {
                    "pci_address": "0000:1a:00.1",
                    "mtu": 1500,
                    "bridge": None,
                    "bond": "bond0",
                    "dpdk_port_name": "dpdk-eno4",
                },
                "eno5": {
                    "pci_address": "0000:ff:00.1",
                    "mtu": 1500,
                    "bridge": "br1",
                    "dpdk_port_name": "dpdk-eno5",
                },
            },
            "bonds": {
                "bond0": {
                    "ports": ["eno3", "eno4"],
                    "bridge": "br-dpdk",
                    "bond_mode": "balance-tcp",
                    "lacp_mode": "active",
                    "lacp_time": "slow",
                    "mtu": 1500,
                }
            },
        }

        hypervisor_snap_mock = mock.Mock()
        hypervisor_snap_mock.present = True
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = dpdk_port_mappings
        mock_get_pci_numa_node.side_effect = lambda address: dpdk_numa_nodes[
            address
        ]

        numa_nodes = self.harness.charm._get_dpdk_numa_nodes()
        expected_numa_nodes = [0, 1]
        self.assertEqual(expected_numa_nodes, numa_nodes)

    @mock.patch("utils.get_pci_numa_node")
    def test_get_dpdk_numa_nodes_no_interfaces(self, mock_get_pci_numa_node):
        """Test retrieving numa nodes, no DPDK ports."""
        self.harness.begin()

        hypervisor_snap_mock = mock.Mock()
        hypervisor_snap_mock.present = True
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = None
        mock_get_pci_numa_node.side_effect = (
            lambda address: "numa-%s" % address
        )

        numa_nodes = self.harness.charm._get_dpdk_numa_nodes()
        expected_numa_nodes = [0]
        self.assertEqual(expected_numa_nodes, numa_nodes)

    @mock.patch("charm.HypervisorOperatorCharm._get_dpdk_settings_override")
    @mock.patch("charm.HypervisorOperatorCharm._get_dpdk_numa_nodes")
    @mock.patch("utils.get_cpu_numa_architecture")
    def _check_handle_ovs_dpdk_mocks(
        self,
        mock_get_numa_architecture,
        mock_get_dpdk_numa_nodes,
        mock_get_dpdk_settings_overide,
        settings_override=None,
        dpdk_numa_nodes=(0,),
        numa_available=True,
        expected_snap_settings=None,
    ):
        mock_get_dpdk_settings_overide.return_value = settings_override or {}
        mock_get_dpdk_numa_nodes.return_value = list(dpdk_numa_nodes)

        if numa_available:
            mock_get_numa_architecture.return_value = {
                0: [0, 2, 4, 6, 8, 10, 12, 14],
                1: [1, 3, 5, 7, 9, 11, 13, 15],
            }
        else:
            mock_get_numa_architecture.return_value = {
                0: [0, 1, 2, 3, 4, 5, 6, 7, 8]
            }

        out = self.harness.charm._handle_ovs_dpdk()
        self.assertEqual(expected_snap_settings, out)

    def test_handle_ovs_dpdk_disabled(self):
        """Disable DPDK and ensure that resources are cleaned up."""
        self.harness.begin()
        expected_snap_settings = {"network.ovs-dpdk-enabled": False}

        self.harness.update_config({"dpdk-enabled": False})
        self._check_handle_ovs_dpdk_mocks(
            expected_snap_settings=expected_snap_settings
        )

        # Ensure that allocations were removed from all numa nodes.
        expected_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 1),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 1),
        ]
        self.harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_calls
        )
        self.assertEqual(
            len(expected_calls),
            self.harness.charm._epa_client.allocate_cores.call_count,
        )

        expected_calls = [
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 0
            ),
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 1
            ),
        ]
        self.harness.charm._epa_client.allocate_hugepages.assert_has_calls(
            expected_calls
        )
        self.assertEqual(
            len(expected_calls),
            self.harness.charm._epa_client.allocate_hugepages.call_count,
        )

    def test_handle_ovs_dpdk_disabled_through_override(self):
        """Check DPDK override."""
        self.harness.begin()
        expected_snap_settings = {"network.ovs-dpdk-enabled": False}
        self.harness.update_config({"dpdk-enabled": True})
        self._check_handle_ovs_dpdk_mocks(
            settings_override={"dpdk-enabled": False},
            expected_snap_settings=expected_snap_settings,
        )

    def test_handle_ovs_dpdk_one_out_of_two_numa_nodes(self):
        """DPDK is configured to use one out of two host numa nodes."""
        self.harness.begin()

        self.harness.charm._epa_client.allocate_cores.side_effect = [
            [0, 2],
            None,
            [4, 6],
            None,
        ]
        self.harness.update_config(
            {
                "dpdk-enabled": True,
                "dpdk-datapath-cores": 2,
                "dpdk-control-plane-cores": 2,
                "dpdk-memory": 2048,
            }
        )
        expected_snap_settings = {
            "network.ovs-dpdk-enabled": True,
            "network.ovs-memory": "2048,0",
            "network.ovs-lcore-mask": "0x5",
            "network.ovs-pmd-cpu-mask": "0x50",
            "network.dpdk-driver": "vfio-pci",
        }
        self._check_handle_ovs_dpdk_mocks(
            expected_snap_settings=expected_snap_settings
        )

        expected_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 1),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 1),
        ]
        self.harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_calls
        )
        self.assertEqual(
            len(expected_calls),
            self.harness.charm._epa_client.allocate_cores.call_count,
        )

        expected_calls = [
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, 2, 1024 * 1024, 0
            ),
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 1
            ),
        ]
        self.harness.charm._epa_client.allocate_hugepages.assert_has_calls(
            expected_calls
        )
        self.assertEqual(
            len(expected_calls),
            self.harness.charm._epa_client.allocate_hugepages.call_count,
        )

    def test_handle_ovs_dpdk_numa_unavailable(self):
        """DPDK is configured to use two out of two host numa nodes.

        The resources are spread across nodes.
        """
        self.harness.begin()

        self.harness.charm._epa_client.allocate_cores.side_effect = [
            [0, 2],
            [4, 6],
        ]
        self.harness.update_config(
            {
                "dpdk-enabled": True,
                "dpdk-datapath-cores": 2,
                "dpdk-control-plane-cores": 2,
                "dpdk-memory": 2048,
                "dpdk-driver": "test-driver",
            }
        )
        expected_snap_settings = {
            "network.ovs-dpdk-enabled": True,
            "network.ovs-memory": "2048",
            "network.ovs-lcore-mask": "0x5",
            "network.ovs-pmd-cpu-mask": "0x50",
            "network.dpdk-driver": "test-driver",
        }
        self._check_handle_ovs_dpdk_mocks(
            expected_snap_settings=expected_snap_settings, numa_available=False
        )

        expected_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, 2, 0),
        ]
        self.harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_calls
        )
        self.assertEqual(
            len(expected_calls),
            self.harness.charm._epa_client.allocate_cores.call_count,
        )

        self.harness.charm._epa_client.allocate_hugepages.assert_called_once_with(
            charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, 2, 1024 * 1024, 0
        )

    @mock.patch("os.path.exists")
    def test_clear_system_ovs_datapaths(self, mock_path_exists):
        """Test clearing system ovs datapaths."""
        self.harness.begin()

        mock_path_exists.return_value = True
        self.subprocess.run.side_effect = [
            mock.Mock(stdout="dp1\ndp2"),
            mock.Mock(stdout=None),
            mock.Mock(stdout=None),
        ]

        self.harness.charm._clear_system_ovs_datapaths()

        self.subprocess.run.assert_has_calls(
            [
                mock.call(
                    ["/usr/bin/ovs-dpctl", "dump-dps"],
                    capture_output=True,
                    text=True,
                    check=True,
                ),
                mock.call(["/usr/bin/ovs-dpctl", "del-dp", "dp1"], check=True),
                mock.call(["/usr/bin/ovs-dpctl", "del-dp", "dp2"], check=True),
            ]
        )

    @mock.patch("os.path.exists")
    def test_clear_system_ovs_datapaths_missing_ovs_dpctl(
        self, mock_path_exists
    ):
        """Test clearing system ovs datapaths, no ovs-dpctl found."""
        self.harness.begin()

        mock_path_exists.return_value = False
        self.harness.charm._clear_system_ovs_datapaths()
        self.subprocess.run.assert_not_called()

    @mock.patch("utils.get_systemd_unit_status")
    @mock.patch.object(
        charm.HypervisorOperatorCharm, "_clear_system_ovs_datapaths"
    )
    def test_disable_system_ovs(self, mock_clear_datapaths, mock_get_status):
        """Test disabling system ovs services."""
        self.harness.begin()

        mock_get_status.side_effect = [
            {
                "name": "openvswitch-switch.service",
                "load_state": "masked",
                "active_state": "active",
                "substate": "running",
            },
            {
                "name": "ovs-vswitchd.service",
                "load_state": "loaded",
                "active_state": "inactive",
                "substate": "dead",
            },
            {
                "name": "ovsdb-server.service",
                "load_state": "loaded",
                "active_state": "active",
                "substate": "running",
            },
            None,
        ]

        ret_val = self.harness.charm._disable_system_ovs()
        self.assertTrue(ret_val)

        self.subprocess.run.assert_has_calls(
            [
                mock.call(
                    ["systemctl", "stop", "openvswitch-switch.service"],
                    check=True,
                ),
                mock.call(
                    ["systemctl", "mask", "ovs-vswitchd.service"], check=True
                ),
                mock.call(
                    ["systemctl", "stop", "ovsdb-server.service"], check=True
                ),
                mock.call(
                    ["systemctl", "mask", "ovsdb-server.service"], check=True
                ),
            ]
        )
        mock_clear_datapaths.assert_called_once_with()

    @mock.patch("utils.get_systemd_unit_status")
    @mock.patch.object(
        charm.HypervisorOperatorCharm, "_clear_system_ovs_datapaths"
    )
    def test_disable_system_ovs_missing(
        self, mock_clear_datapaths, mock_get_status
    ):
        """Test disabling system ovs, no services found."""
        self.harness.begin()
        mock_get_status.return_value = None

        ret_val = self.harness.charm._disable_system_ovs()
        self.assertFalse(ret_val)
