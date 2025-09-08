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

"""Define openstack-network-agents tests."""

from unittest.mock import (
    MagicMock,
    patch,
)

import charm
import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.test_utils as test_utils


class _OpenstackNetworkAgentsOperatorCharm(
    charm.OpenstackNetworkAgentsOperatorCharm
):
    def __init__(self, framework):
        self.seen = []
        super().__init__(framework)


class TestOpenstackNetworkAgentsCharm(test_utils.CharmTestCase):
    """Tests for Openstack Network Agents charm."""

    PATCHES = []

    def setUp(self):
        """Set up the test harness."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _OpenstackNetworkAgentsOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def test_validated_network_config_ok(self):
        """Test validated network config with all required fields."""
        self.harness.begin()
        self.harness.update_config(
            {
                "external-interface": "ens10",
                "bridge-name": "br-ex",
                "physnet-name": "physnet1",
                "enable-chassis-as-gw": True,
            }
        )
        iface, bridge, physnet, enable_gw = (
            self.harness.charm._validated_network_config()
        )
        assert iface == "ens10"
        assert bridge == "br-ex"
        assert physnet == "physnet1"
        assert enable_gw is True

    def test_validated_network_config_missing(self):
        """Test validated network config with missing fields."""
        self.harness.begin()
        self.harness.update_config(
            {
                "external-interface": "ens10",
                "bridge-name": "br-ex",
                "enable-chassis-as-gw": False,
            }
        )
        with self.assertRaises(sunbeam_guard.BlockedExceptionError) as ctx:
            self.harness.charm._validated_network_config()
        msg = str(ctx.exception)
        assert "physnet-name" in msg
        assert "enable-chassis-as-gw" in msg

    def _fake_snap_cache(self, *, microovn_present=True, connect_raises=None):
        """Return a fake snap cache with controlled snap presence and behavior."""
        openstack_snap = MagicMock(name="openstack-network-agents")
        if connect_raises:
            openstack_snap.connect.side_effect = connect_raises

        microovn_snap = MagicMock(name="microovn")
        microovn_snap.present = microovn_present

        class _Cache:
            def get(self, name):
                if name == "openstack-network-agents":
                    return openstack_snap
                if name == "microovn":
                    return microovn_snap
                raise KeyError(name)

        return _Cache(), openstack_snap, microovn_snap

    def test_connect_ovn_chassis_happy_path(self):
        """Test _connect_ovn_chassis when microovn is present."""
        self.harness.begin()
        cache, openstack_snap, _ = self._fake_snap_cache(microovn_present=True)
        with patch.object(
            self.harness.charm, "get_snap_cache", return_value=cache
        ):
            self.harness.charm._connect_ovn_chassis()
        openstack_snap.connect.assert_called_once_with(
            charm.OVN_CHASSIS_PLUG, slot=charm.OVN_CHASSIS_SLOT
        )

    def test_connect_ovn_chassis_skips_when_microovn_not_present(self):
        """Test _connect_ovn_chassis when microovn is not present."""
        self.harness.begin()
        cache, openstack_snap, _ = self._fake_snap_cache(
            microovn_present=False
        )
        with patch.object(
            self.harness.charm, "get_snap_cache", return_value=cache
        ):
            self.harness.charm._connect_ovn_chassis()
        openstack_snap.connect.assert_not_called()

    def test_connect_ovn_chassis_skips_when_microovn_missing_in_cache(self):
        """Test _connect_ovn_chassis when microovn is missing in snap cache."""
        self.harness.begin()

        class _Cache:
            def get(self, name):
                if name == "openstack-network-agents":
                    return MagicMock(name="openstack-network-agents")
                if name == "microovn":
                    raise snap.SnapNotFoundError("microovn")
                raise KeyError(name)

        with patch.object(
            self.harness.charm, "get_snap_cache", return_value=_Cache()
        ):
            self.harness.charm._connect_ovn_chassis()

    def test_connect_ovn_chassis_snap_error(self):
        """Test _connect_ovn_chassis when snap connect raises SnapError."""
        self.harness.begin()
        err = snap.SnapError("connect failed")
        cache, _, _ = self._fake_snap_cache(connect_raises=err)
        with patch.object(
            self.harness.charm, "get_snap_cache", return_value=cache
        ):
            with self.assertRaises(snap.SnapError):
                self.harness.charm._connect_ovn_chassis()

    def test_configure_snap_sets_snap_data_and_connects(self):
        """Test configure_snap sets snap data and connects ovn-chassis."""
        self.harness.begin()
        self.harness.update_config(
            {
                "external-interface": "ens10",
                "bridge-name": "br-ex",
                "physnet-name": "physnet1",
                "enable-chassis-as-gw": True,
                "debug": True,
            }
        )

        with patch.object(
            self.harness.charm, "set_snap_data"
        ) as set_snap_data_mock, patch.object(
            self.harness.charm, "_connect_ovn_chassis"
        ) as connect_mock:
            evt = MagicMock(spec=ops.EventBase)
            self.harness.charm.configure_snap(evt)

        connect_mock.assert_called_once()
        set_snap_data_mock.assert_called_once()
        (kwargs,), _ = set_snap_data_mock.call_args
        assert kwargs == {
            "network.interface": "ens10",
            "network.bridge": "br-ex",
            "network.physnet": "physnet1",
            "network.enable-chassis-as-gw": True,
            "settings.debug": True,
        }
