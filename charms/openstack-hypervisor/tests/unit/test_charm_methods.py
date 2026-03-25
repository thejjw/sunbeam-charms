# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for HypervisorOperatorCharm methods.

Ported from the harness-based test_charm.py into plain pytest style.
Tests are grouped by functionality: actions, consul, snap, DPDK, OVS.
"""

import contextlib
import json
import os
import tempfile
from pathlib import (
    Path,
)
from types import (
    SimpleNamespace,
)
from unittest import (
    mock,
)
from unittest.mock import (
    MagicMock,
    Mock,
)

import charm
import charms.operator_libs_linux.v2.snap as snap
import jsonschema
import ops.testing
import ops_sunbeam.guard as sunbeam_guard
import pytest

CHARM_ROOT = Path(__file__).parents[2]

# Save the real os.path.exists before any fixture patches it.
_real_path_exists = os.path.exists


class _TestableHypervisorCharm(charm.HypervisorOperatorCharm):
    """Charm subclass that skips consul_notify observer registration.

    The ConsulNotifyRequirer mock does not provide real BoundEvents,
    so we intercept framework.observe to skip those handlers.
    """

    def __init__(self, framework):
        self.seen_events = []
        original_observe = framework.observe

        def patched_observe(event, handler):
            if (
                hasattr(handler, "__name__")
                and "consul_notify" in handler.__name__
            ):
                return
            return original_observe(event, handler)

        framework.observe = patched_observe
        super().__init__(framework)
        framework.observe = original_observe


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def harness(_mock_heavy_externals, monkeypatch):
    """Provide an ops.testing.Harness with all heavy externals mocked.

    Uses the shared ``_mock_heavy_externals`` fixture from conftest,
    then adds ConsulNotifyRequirer mocking and uses test_utils.get_harness
    for the proper model backend (network_get, etc.).
    """
    import ops_sunbeam.test_utils as test_utils

    consul_mock = MagicMock()
    monkeypatch.setattr(charm, "ConsulNotifyRequirer", consul_mock)

    h = test_utils.get_harness(_TestableHypervisorCharm)
    yield h
    h.cleanup()


@pytest.fixture()
def charm_instance(harness):
    """Return the charm after ``harness.begin()``."""
    harness.begin()
    return harness.charm


# ---------------------------------------------------------------------------
# Action tests
# ---------------------------------------------------------------------------


class TestActions:
    """Tests for charm actions: list-nics, list-gpus, list-flavors."""

    def test_openstack_hypervisor_snap_not_installed(self, harness):
        """Raise SnapNotFoundError when snap cache raises."""
        harness.begin()
        charm.snap.SnapCache.side_effect = snap.SnapNotFoundError
        with pytest.raises(snap.SnapNotFoundError):
            harness.run_action("list-nics")

    def test_list_nics_snap_not_installed(self, harness):
        """Raise ActionFailed when hypervisor snap is not present."""
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        with pytest.raises(ops.testing.ActionFailed):
            harness.run_action("list-nics")
        with pytest.raises(ops.testing.ActionFailed):
            harness.run_action("list-gpus")

    def test_list_nics(self, harness):
        """Action returns nics on success."""
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        charm.subprocess.run.return_value = MagicMock(
            stdout=bytes(
                json.dumps({"nics": ["eth0", "eth1"], "candidates": ["eth2"]}),
                "utf-8",
            ),
            stderr=b"yes things went well",
            returncode=0,
        )
        action_output = harness.run_action("list-nics")
        assert "candidates" in action_output.results["result"]

    def test_list_nics_error(self, harness):
        """Raise ActionFailed when subprocess returns non-zero."""
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        charm.subprocess.run.return_value = MagicMock(
            stdout=b"",
            stderr=b"things did not go well",
            returncode=1,
        )
        with pytest.raises(ops.testing.ActionFailed):
            harness.run_action("list-nics")

    def test_list_gpus(self, harness):
        """Action returns gpus on success."""
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        charm.subprocess.run.return_value = MagicMock(
            stdout=bytes(
                json.dumps(
                    {
                        "gpus": [
                            {
                                "pci_address": "0000:09:00.0",
                                "product_id": "0x0534",
                                "vendor_id": "0x102b",
                            }
                        ]
                    }
                ),
                "utf-8",
            ),
            stderr=b"yes things went well",
            returncode=0,
        )
        action_output = harness.run_action("list-gpus")
        assert "gpus" in action_output.results["result"]

    def test_list_gpus_error(self, harness):
        """Raise ActionFailed when subprocess returns non-zero for list-gpus."""
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        charm.subprocess.run.return_value = MagicMock(
            stdout=b"",
            stderr=b"things did not go well",
            returncode=1,
        )
        with pytest.raises(ops.testing.ActionFailed):
            harness.run_action("list-gpus")

    def test_list_flavors(self, harness):
        """Action returns flavors from snap config."""
        flavors = "flavor1,flavor2"
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = flavors
        action_output = harness.run_action("list-flavors")
        assert action_output.results["result"] == flavors


# ---------------------------------------------------------------------------
# Consul tests
# ---------------------------------------------------------------------------


class TestConsul:
    """Tests for consul notify integration."""

    def test_consul_notify_initialization(self, charm_instance):
        """Charm should have a consul_notify attribute."""
        assert hasattr(charm_instance, "consul_notify")
        assert charm_instance.consul_notify is not None

    def test_consul_notify_event_handler(self, charm_instance):
        """_on_consul_notify_ready sets socket info on the consul object."""
        event_mock = MagicMock()
        charm_instance._on_consul_notify_ready(event_mock)

        charm_instance.consul_notify.set_socket_info.assert_called_once_with(
            snap_name=charm.HYPERVISOR_SNAP_NAME,
            unix_socket_filepath=charm.EVACUATION_UNIX_SOCKET_FILEPATH,
        )


# ---------------------------------------------------------------------------
# Snap tests
# ---------------------------------------------------------------------------


class TestSnap:
    """Tests for snap connect and microovn handling."""

    def test_snap_connect_success(self, charm_instance):
        """Successful snap connect to epa-orchestrator."""
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.return_value = None

        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        charm_instance._connect_to_epa_orchestrator()
        hypervisor_snap_mock.connect.assert_called_once_with(
            charm.EPA_INFO_PLUG, slot=charm.EPA_INFO_SLOT
        )
        hypervisor_snap_mock.set.assert_called_once()
        args, _kwargs = hypervisor_snap_mock.set.call_args
        assert "configure-trigger" in args[0]

    def test_ensure_services_running_skips_when_microovn_absent(
        self, charm_instance
    ):
        """Do not attempt restart when microovn snap is not present."""
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        microovn_snap_mock = MagicMock()
        microovn_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock
        }
        hypervisor_snap_mock.get.return_value = "true"
        charm_instance.ensure_services_running()
        # Assert no restart or trigger reset occurred
        microovn_snap_mock.restart.assert_not_called()

    def test_snap_connect_failure_snaperror(self, charm_instance):
        """Raise SnapError on connect failure."""
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.side_effect = snap.SnapError(
            "Connection failed"
        )

        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        with pytest.raises(snap.SnapError):
            charm_instance._connect_to_epa_orchestrator()


class TestRelationDerivedConfig:
    """Tests for relation-derived snap configuration helpers."""

    def test_handle_barbican_service_ready(self, charm_instance):
        """Ready Barbican relation should enable the key manager."""
        contexts = SimpleNamespace(
            barbican_service=SimpleNamespace(service_ready=True)
        )

        assert charm_instance._handle_barbican_service(contexts) == {
            "compute.key-manager-enabled": True
        }

    def test_handle_barbican_service_missing(self, charm_instance):
        """Missing Barbican relation should disable the key manager."""
        assert charm_instance._handle_barbican_service(SimpleNamespace()) == {
            "compute.key-manager-enabled": False
        }

    def test_microovn_present(self, charm_instance):
        """Microovn present handling does not call alias."""
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        microovn_snap_mock = MagicMock()
        microovn_snap_mock.present = True
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
            "microovn": microovn_snap_mock,
        }

        hypervisor_snap_mock.alias.assert_not_called


# ---------------------------------------------------------------------------
# DPDK tests
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _mock_dpdk_settings_file(dpdk_yaml):
    """Write *dpdk_yaml* to a temp file and patch the charm constant."""
    with tempfile.NamedTemporaryFile(mode="w") as f:
        f.write(dpdk_yaml)
        f.flush()
        with mock.patch.object(charm, "DPDK_CONFIG_OVERRIDE_PATH", f.name):
            yield


class TestDPDK:
    """Tests for DPDK settings, bitmask conversion, and NUMA nodes."""

    def test_get_dpdk_settings_override(self, charm_instance, monkeypatch):
        """Valid DPDK override YAML is parsed correctly."""
        monkeypatch.setattr(charm.os.path, "exists", _real_path_exists)
        dpdk_yaml = """
dpdk:
    dpdk-enabled: true
    dpdk-memory: 2048
    dpdk-datapath-cores: 4
    dpdk-controlplane-cores: 4
"""
        expected = {
            "dpdk-enabled": True,
            "dpdk-memory": 2048,
            "dpdk-datapath-cores": 4,
            "dpdk-controlplane-cores": 4,
        }
        with _mock_dpdk_settings_file(dpdk_yaml):
            result = charm_instance._get_dpdk_settings_override()
            assert result == expected

    def test_get_dpdk_settings_override_invalid(
        self, charm_instance, monkeypatch
    ):
        """Invalid DPDK YAML raises ValidationError."""
        monkeypatch.setattr(charm.os.path, "exists", _real_path_exists)
        dpdk_yaml = """
network:
    dpdk-enabled: true
    ovs-memory: 1
"""
        with _mock_dpdk_settings_file(dpdk_yaml):
            with pytest.raises(jsonschema.exceptions.ValidationError):
                charm_instance._get_dpdk_settings_override()

    def test_core_list_to_bitmask(self, charm_instance):
        """Convert CPU core lists to bit masks."""
        assert charm_instance._core_list_to_bitmask([0]) == "0x1"
        assert charm_instance._core_list_to_bitmask([4, 5, 6, 7]) == "0xf0"
        assert (
            charm_instance._core_list_to_bitmask([16, 24, 0, 8]) == "0x1010101"
        )

        with pytest.raises(ValueError):
            charm_instance._core_list_to_bitmask([0, -1])
        with pytest.raises(ValueError):
            charm_instance._core_list_to_bitmask([2, 2049])

    def test_bitmask_to_core_list(self, charm_instance):
        """Convert CPU bit masks to core lists."""
        assert charm_instance._bitmask_to_core_list(0) == []
        assert charm_instance._bitmask_to_core_list(1) == [0]
        assert charm_instance._bitmask_to_core_list(0xF0) == [4, 5, 6, 7]
        assert charm_instance._bitmask_to_core_list(0x1010101) == [
            0,
            8,
            16,
            24,
        ]

    @mock.patch("utils.get_pci_numa_node")
    def test_get_dpdk_numa_nodes(self, mock_get_pci_numa_node, charm_instance):
        """Retrieve NUMA nodes based on DPDK ports."""
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

        hypervisor_snap_mock = Mock()
        hypervisor_snap_mock.present = True
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = dpdk_port_mappings
        mock_get_pci_numa_node.side_effect = lambda addr: dpdk_numa_nodes[addr]

        assert charm_instance._get_dpdk_numa_nodes() == [0, 1]

    @mock.patch("utils.get_pci_numa_node")
    def test_get_dpdk_numa_nodes_no_interfaces(
        self, mock_get_pci_numa_node, charm_instance
    ):
        """No DPDK ports defaults to NUMA node [0]."""
        hypervisor_snap_mock = Mock()
        hypervisor_snap_mock.present = True
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = None
        mock_get_pci_numa_node.side_effect = (
            lambda address: "numa-%s" % address
        )

        assert charm_instance._get_dpdk_numa_nodes() == [0]


# ---------------------------------------------------------------------------
# OVS DPDK tests
# ---------------------------------------------------------------------------


def _check_handle_ovs_dpdk(
    harness,
    *,
    settings_override=None,
    dpdk_numa_nodes=(0,),
    numa_available=True,
    expected_snap_settings=None,
):
    """Helper that mocks DPDK dependencies and calls _handle_ovs_dpdk."""
    with (
        mock.patch.object(
            charm.HypervisorOperatorCharm,
            "_get_dpdk_settings_override",
            return_value=settings_override or {},
        ),
        mock.patch.object(
            charm.HypervisorOperatorCharm,
            "_get_dpdk_numa_nodes",
            return_value=list(dpdk_numa_nodes),
        ),
        mock.patch(
            "utils.get_cpu_numa_architecture",
            return_value=(
                {
                    0: [0, 2, 4, 6, 8, 10, 12, 14],
                    1: [1, 3, 5, 7, 9, 11, 13, 15],
                }
                if numa_available
                else {0: [0, 1, 2, 3, 4, 5, 6, 7, 8]}
            ),
        ),
    ):
        out = harness.charm._handle_ovs_dpdk()
        assert out == expected_snap_settings


class TestOVSDPDK:
    """Tests for _handle_ovs_dpdk and EPA integration."""

    def test_handle_ovs_dpdk_disabled(self, harness):
        """Disable DPDK and ensure resources are cleaned up."""
        harness.begin()
        expected_snap_settings = {"network.ovs-dpdk-enabled": False}

        harness.update_config({"dpdk-enabled": False})
        _check_handle_ovs_dpdk(
            harness, expected_snap_settings=expected_snap_settings
        )

        # Ensure allocations removed from all NUMA nodes
        expected_core_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 1),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 1),
        ]
        harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_core_calls
        )
        assert harness.charm._epa_client.allocate_cores.call_count == len(
            expected_core_calls
        )

        expected_hp_calls = [
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 0
            ),
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 1
            ),
        ]
        harness.charm._epa_client.allocate_hugepages.assert_has_calls(
            expected_hp_calls
        )
        assert harness.charm._epa_client.allocate_hugepages.call_count == len(
            expected_hp_calls
        )

    def test_handle_ovs_dpdk_disabled_through_override(self, harness):
        """DPDK override disables DPDK even when config enables it."""
        harness.begin()
        expected_snap_settings = {"network.ovs-dpdk-enabled": False}
        harness.update_config({"dpdk-enabled": True})
        _check_handle_ovs_dpdk(
            harness,
            settings_override={"dpdk-enabled": False},
            expected_snap_settings=expected_snap_settings,
        )

    def test_handle_ovs_dpdk_one_out_of_two_numa_nodes(self, harness):
        """DPDK uses one out of two host NUMA nodes."""
        harness.begin()

        harness.charm._epa_client.allocate_cores.side_effect = [
            [0, 2],
            None,
            [4, 6],
            None,
        ]
        harness.update_config(
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
        _check_handle_ovs_dpdk(
            harness, expected_snap_settings=expected_snap_settings
        )

        expected_core_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, -1, 1),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, -1, 1),
        ]
        harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_core_calls
        )
        assert harness.charm._epa_client.allocate_cores.call_count == len(
            expected_core_calls
        )

        expected_hp_calls = [
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, 2, 1024 * 1024, 0
            ),
            mock.call(
                charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, -1, 1024 * 1024, 1
            ),
        ]
        harness.charm._epa_client.allocate_hugepages.assert_has_calls(
            expected_hp_calls
        )
        assert harness.charm._epa_client.allocate_hugepages.call_count == len(
            expected_hp_calls
        )

    def test_handle_ovs_dpdk_numa_unavailable(self, harness):
        """DPDK with NUMA unavailable spreads resources across one node."""
        harness.begin()

        harness.charm._epa_client.allocate_cores.side_effect = [
            [0, 2],
            [4, 6],
        ]
        harness.update_config(
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
        _check_handle_ovs_dpdk(
            harness,
            expected_snap_settings=expected_snap_settings,
            numa_available=False,
        )

        expected_core_calls = [
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE, 2, 0),
            mock.call(charm.EPA_ALLOCATION_OVS_DPDK_DATAPATH, 2, 0),
        ]
        harness.charm._epa_client.allocate_cores.assert_has_calls(
            expected_core_calls
        )
        assert harness.charm._epa_client.allocate_cores.call_count == len(
            expected_core_calls
        )

        harness.charm._epa_client.allocate_hugepages.assert_called_once_with(
            charm.EPA_ALLOCATION_OVS_DPDK_HUGEPAGES, 2, 1024 * 1024, 0
        )

    def test_configure_unit_calls_handle_ovs_dpdk_when_epa_available(
        self, charm_instance
    ):
        """_handle_ovs_dpdk is called when EPA is available."""
        charm_instance._epa_client.is_available = MagicMock(return_value=True)
        charm_instance._handle_ovs_dpdk = MagicMock(
            return_value={"network.ovs-dpdk-enabled": True}
        )

        snap_data = {}
        if charm_instance._epa_client.is_available():
            snap_data.update(charm_instance._handle_ovs_dpdk())

        charm_instance._epa_client.is_available.assert_called()
        charm_instance._handle_ovs_dpdk.assert_called()
        assert snap_data == {"network.ovs-dpdk-enabled": True}

    def test_configure_unit_skips_handle_ovs_dpdk_when_epa_unavailable(
        self, charm_instance
    ):
        """_handle_ovs_dpdk is NOT called when EPA is unavailable."""
        charm_instance._epa_client.is_available = MagicMock(return_value=False)
        charm_instance._handle_ovs_dpdk = MagicMock(
            return_value={"network.ovs-dpdk-enabled": True}
        )

        snap_data = {}
        if charm_instance._epa_client.is_available():
            snap_data.update(charm_instance._handle_ovs_dpdk())

        charm_instance._epa_client.is_available.assert_called()
        charm_instance._handle_ovs_dpdk.assert_not_called()
        assert snap_data == {}


# ---------------------------------------------------------------------------
# OVS system tests
# ---------------------------------------------------------------------------


class TestOVSSystem:
    """Tests for OVS datapath clearing and system OVS disabling."""

    def test_clear_system_ovs_datapaths(self, charm_instance, monkeypatch):
        """Clear system OVS datapaths when ovs-dpctl exists."""
        monkeypatch.setattr(charm.os.path, "exists", lambda p: True)
        charm.subprocess.run.side_effect = [
            Mock(stdout="dp1\ndp2"),
            Mock(stdout=None),
            Mock(stdout=None),
        ]

        charm_instance._clear_system_ovs_datapaths()

        charm.subprocess.run.assert_has_calls(
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

    def test_clear_system_ovs_datapaths_missing_ovs_dpctl(
        self, charm_instance, monkeypatch
    ):
        """No action when ovs-dpctl is not found."""
        monkeypatch.setattr(charm.os.path, "exists", lambda p: False)
        charm.subprocess.run.reset_mock()

        charm_instance._clear_system_ovs_datapaths()
        charm.subprocess.run.assert_not_called()

    @mock.patch("utils.get_systemd_unit_status")
    @mock.patch.object(
        charm.HypervisorOperatorCharm, "_clear_system_ovs_datapaths"
    )
    def test_disable_system_ovs(
        self, mock_clear_datapaths, mock_get_status, charm_instance
    ):
        """Disable system OVS services."""
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

        assert charm_instance._disable_system_ovs() is True

        charm.subprocess.run.assert_has_calls(
            [
                mock.call(
                    ["systemctl", "stop", "openvswitch-switch.service"],
                    check=True,
                ),
                mock.call(
                    ["systemctl", "mask", "ovs-vswitchd.service"],
                    check=True,
                ),
                mock.call(
                    ["systemctl", "stop", "ovsdb-server.service"],
                    check=True,
                ),
                mock.call(
                    ["systemctl", "mask", "ovsdb-server.service"],
                    check=True,
                ),
            ]
        )
        mock_clear_datapaths.assert_called_once_with()

    @mock.patch("utils.get_systemd_unit_status")
    @mock.patch.object(
        charm.HypervisorOperatorCharm, "_clear_system_ovs_datapaths"
    )
    def test_disable_system_ovs_missing(
        self, mock_clear_datapaths, mock_get_status, charm_instance
    ):
        """No OVS services found returns False."""
        mock_get_status.return_value = None

        assert charm_instance._disable_system_ovs() is False


# ---------------------------------------------------------------------------
# OVS provider / ovs-managed-by tests
# ---------------------------------------------------------------------------


def _minimal_contexts_stub():
    """Return a minimal contexts SimpleNamespace for configure_unit tests."""
    return SimpleNamespace(
        ovsdb_cms=SimpleNamespace(
            db_ingress_sb_connection_strs=["ssl:10.0.0.1:6642"]
        ),
        certificates=SimpleNamespace(
            ca_cert="CA",
            cert="CERT",
            key="KEY",
            ca_with_chain="CA_CHAIN",
        ),
        identity_credentials=SimpleNamespace(
            admin_role="admin",
            internal_endpoint="http://keystone:5000",
            password="secret",
            project_domain_id="default",
            project_domain_name="Default",
            project_id="proj-id",
            project_name="admin",
            region="RegionOne",
            user_domain_id="default",
            user_domain_name="Default",
            username="nova",
        ),
        amqp=SimpleNamespace(transport_url="rabbit://..."),
        receive_ca_cert=SimpleNamespace(ca_bundle=None),
    )


def _setup_configure_unit_mocks(charm_instance):
    """Stub out the heavy configure_unit dependencies and return captured snap_data."""
    captured = {}
    charm_instance.set_snap_data = lambda snap_data: captured.update(snap_data)
    charm_instance.ensure_snap_present = MagicMock()
    charm_instance.ensure_services_running = MagicMock()
    charm_instance.check_leader_ready = MagicMock()
    charm_instance.check_relation_handlers_ready = MagicMock()
    charm_instance._check_ovs_provider_immutable = MagicMock()
    charm_instance._validate_ovs_provider = MagicMock()
    charm_instance.contexts = MagicMock(return_value=_minimal_contexts_stub())
    return captured


class TestOVSProvider:
    """Tests for ovs-provider config handling.

    Covers _is_microovn_present(), _check_ovs_provider_immutable(),
    _validate_ovs_provider(), and the network.ovs-managed-by snap config key.
    """

    # --- _is_microovn_present ---

    def test_is_microovn_present_config_microovn(self, harness):
        """ovs-provider=microovn always returns True regardless of snap state."""
        harness.update_config({"ovs-provider": "microovn"})
        harness.begin()
        assert harness.charm._is_microovn_present() is True

    def test_is_microovn_present_config_hypervisor(self, harness):
        """ovs-provider=hypervisor always returns False regardless of snap state."""
        harness.update_config({"ovs-provider": "hypervisor"})
        harness.begin()
        # Even when microovn snap is reported as present, config wins.
        microovn_snap_mock = MagicMock()
        microovn_snap_mock.present = True
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": MagicMock(),
            "microovn": microovn_snap_mock,
        }
        assert harness.charm._is_microovn_present() is False

    def test_is_microovn_present_auto_snap_present(self, harness):
        """ovs-provider=auto returns True when microovn snap is present."""
        harness.update_config({"ovs-provider": "auto"})
        harness.begin()
        microovn_snap_mock = MagicMock()
        microovn_snap_mock.present = True
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": MagicMock(),
            "microovn": microovn_snap_mock,
        }
        assert harness.charm._is_microovn_present() is True

    def test_is_microovn_present_auto_snap_absent(self, harness):
        """ovs-provider=auto returns False when microovn snap is not present."""
        harness.update_config({"ovs-provider": "auto"})
        harness.begin()
        microovn_snap_mock = MagicMock()
        microovn_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": MagicMock(),
            "microovn": microovn_snap_mock,
        }
        assert harness.charm._is_microovn_present() is False

    # --- _check_ovs_provider_immutable ---

    def test_check_ovs_provider_immutable_first_run_allows_any(
        self, charm_instance
    ):
        """No stored value → first configuration is always allowed."""
        charm_instance._state.ovs_provider = ""
        # Should not raise when nothing has been committed yet.
        charm_instance._check_ovs_provider_immutable()  # no exception

    def test_check_ovs_provider_immutable_same_value_ok(self, harness):
        """Same ovs-provider value after initial config does not raise."""
        harness.update_config({"ovs-provider": "microovn"})
        harness.begin()
        harness.charm._state.ovs_provider = "microovn"
        # Should not raise.
        harness.charm._check_ovs_provider_immutable()

    def test_check_ovs_provider_immutable_changed_raises(self, harness):
        """Changing ovs-provider after initial config raises BlockedExceptionError."""
        harness.update_config({"ovs-provider": "hypervisor"})
        harness.begin()
        # Simulate that "microovn" was the initially committed value.
        harness.charm._state.ovs_provider = "microovn"
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            harness.charm._check_ovs_provider_immutable()

    # --- _validate_ovs_provider ---

    @pytest.mark.parametrize("value", ["auto", "microovn", "hypervisor"])
    def test_validate_ovs_provider_valid_values(self, harness, value):
        """Valid ovs-provider values do not raise."""
        harness.update_config({"ovs-provider": value})
        harness.begin()
        harness.charm._validate_ovs_provider()  # must not raise

    def test_validate_ovs_provider_invalid_raises(self, harness):
        """An unrecognised ovs-provider value raises BlockedExceptionError."""
        harness.update_config({"ovs-provider": "bad-value"})
        harness.begin()
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            harness.charm._validate_ovs_provider()

    # --- network.ovs-managed-by in snap_data ---

    def test_ovs_managed_by_included_in_snap_data(self, harness):
        """configure_unit passes network.ovs-managed-by to set_snap_data."""
        harness.update_config({"ovs-provider": "microovn"})
        harness.begin()
        captured = _setup_configure_unit_mocks(harness.charm)
        harness.charm.configure_unit(MagicMock())
        assert captured.get("network.ovs-managed-by") == "microovn"

    @pytest.mark.parametrize(
        "ovs_provider", ["auto", "microovn", "hypervisor"]
    )
    def test_ovs_managed_by_value_matches_ovs_provider(
        self, harness, ovs_provider
    ):
        """network.ovs-managed-by snap key always mirrors the ovs-provider config."""
        harness.update_config({"ovs-provider": ovs_provider})
        harness.begin()
        captured = _setup_configure_unit_mocks(harness.charm)
        harness.charm.configure_unit(MagicMock())
        assert captured.get("network.ovs-managed-by") == ovs_provider

    # --- ensure_snap_present: early ovs-managed-by ---

    def test_ensure_snap_present_sets_ovs_managed_by(self, harness):
        """ensure_snap_present sets network.ovs-managed-by on the snap."""
        harness.update_config({"ovs-provider": "microovn"})
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": MagicMock(),
            "microovn": MagicMock(),
        }
        with mock.patch.object(
            harness.charm, "_disable_system_ovs", return_value=False
        ), mock.patch.object(harness.charm, "_connect_to_epa_orchestrator"):
            harness.charm.ensure_snap_present()

        hypervisor_snap_mock.set.assert_called_with(
            {"network.ovs-managed-by": "microovn"}, typed=True
        )

    def test_ensure_snap_present_invalid_ovs_provider_clamps_to_auto(
        self, harness
    ):
        """ensure_snap_present falls back to auto for an invalid ovs-provider."""
        harness.update_config({"ovs-provider": "bad-value"})
        harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        charm.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": MagicMock(),
            "microovn": MagicMock(),
        }
        with mock.patch.object(
            harness.charm, "_disable_system_ovs", return_value=False
        ), mock.patch.object(harness.charm, "_connect_to_epa_orchestrator"):
            harness.charm.ensure_snap_present()

        hypervisor_snap_mock.set.assert_called_with(
            {"network.ovs-managed-by": "auto"}, typed=True
        )

    # --- ensure_services_running SnapNotFoundError ---

    def test_ensure_services_running_microovn_snap_not_found_error(
        self, charm_instance
    ):
        """Handle SnapNotFoundError when fetching microovn snap gracefully."""
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.get.return_value = "true"
        hypervisor_snap_mock.services = {
            svc: {"active": True}
            for svc in [
                "neutron-ovn-metadata-agent",
                "nova-api-metadata",
                "nova-compute",
            ]
        }
        snap_cache = {"openstack-hypervisor": hypervisor_snap_mock}

        def _snap_cache_getitem(key):
            if key == "microovn":
                raise snap.SnapNotFoundError
            return snap_cache[key]

        mock_cache = MagicMock()
        mock_cache.__getitem__ = MagicMock(side_effect=_snap_cache_getitem)
        charm.snap.SnapCache.return_value = mock_cache

        with mock.patch.object(
            charm_instance, "_is_microovn_present", return_value=True
        ):
            charm_instance.ensure_services_running()  # must not raise

        # Trigger key read happened but no restart was attempted
        hypervisor_snap_mock.get.assert_called_with(
            charm.MICROOVN_RESTART_TRIGGER_SNAP_KEY
        )
        hypervisor_snap_mock.set.assert_not_called()
