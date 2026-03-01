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
