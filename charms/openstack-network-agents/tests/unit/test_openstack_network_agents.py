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
import ops
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

    def test_set_network_agents_local_settings_action(self):
        """Test set_network_agents_local_settings_action."""
        self.harness.begin()
        evt = MagicMock(spec=ops.ActionEvent)
        evt.params = {
            "external-interface": "ens10",
            "bridge-name": "br-ex",
            "physnet-name": "physnet1",
            "enable-chassis-as-gw": True,
        }

        with patch.object(
            self.harness.charm, "set_snap_data"
        ) as mock_set_snap_data:
            self.harness.charm._set_network_agents_local_settings_action(evt)

            mock_set_snap_data.assert_called_once_with(
                {
                    "network.external-interface": "ens10",
                    "network.bridge-name": "br-ex",
                    "network.physnet-name": "physnet1",
                    "network.enable-chassis-as-gw": True,
                }
            )

    def test_set_network_agents_local_settings_action_with_bridge_mapping(
        self,
    ):
        """Test set_network_agents_local_settings_action with bridge mapping."""
        self.harness.begin()
        evt = MagicMock(spec=ops.ActionEvent)
        evt.params = {
            "bridge-mapping": "physnet1:br-ex",
            "enable-chassis-as-gw": True,
        }

        with patch.object(
            self.harness.charm, "set_snap_data"
        ) as mock_set_snap_data:
            self.harness.charm._set_network_agents_local_settings_action(evt)

            mock_set_snap_data.assert_called_once_with(
                {
                    "network.bridge-mapping": "physnet1:br-ex",
                    "network.enable-chassis-as-gw": True,
                }
            )

    def test_connect_ovn_chassis_success(self):
        """Test _connect_ovn_chassis when microovn is present."""
        self.harness.begin()
        mock_snap = MagicMock(name="openstack-network-agents")
        with patch.object(
            self.harness.charm, "get_snap", return_value=mock_snap
        ):
            self.harness.charm._connect_ovn_chassis()
        mock_snap.connect.assert_called_once_with(
            charm.OVN_CHASSIS_PLUG, slot=charm.OVN_CHASSIS_SLOT
        )

    def test_connect_ovn_chassis_errors_out(self):
        """Test _connect_ovn_chassis when snap connect raises SnapError."""
        self.harness.begin()
        mock_snap = MagicMock(name="openstack-network-agents")
        mock_snap.connect.side_effect = Exception("boom")

        with patch.object(
            self.harness.charm, "get_snap", return_value=mock_snap
        ):
            with self.assertRaises(Exception):
                self.harness.charm._connect_ovn_chassis()

    def test_configure_snap_sets_snap_data_and_connects(self):
        """configure_snap connects ovn-chassis and pushes snap data."""
        self.harness.begin()
        self.harness.update_config({"debug": True})

        mock_snap = MagicMock(name="openstack-network-agents")
        with patch.object(
            self.harness.charm, "get_snap", return_value=mock_snap
        ), patch.object(
            self.harness.charm, "set_snap_data"
        ) as set_snap_data_mock:
            evt = MagicMock(spec=ops.EventBase)
            self.harness.charm.configure_snap(evt)

        mock_snap.connect.assert_called_once_with(
            charm.OVN_CHASSIS_PLUG, slot=charm.OVN_CHASSIS_SLOT
        )
        set_snap_data_mock.assert_called_once()
        (kwargs,), _ = set_snap_data_mock.call_args
        assert kwargs == {
            "settings.debug": True,
        }
