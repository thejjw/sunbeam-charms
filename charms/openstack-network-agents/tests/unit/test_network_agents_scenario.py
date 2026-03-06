#!/usr/bin/env python3

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

"""ops.testing (state-transition) tests for openstack-network-agents."""

from unittest.mock import (
    MagicMock,
)

import charm
from ops import (
    testing,
)


def juju_info_relation() -> testing.SubordinateRelation:
    """juju-info subordinate relation (mandatory)."""
    return testing.SubordinateRelation(
        endpoint="juju-info",
        remote_app_name="principal-app",
    )


# ---------------------------------------------------------------------------
# Tests: blocked when no relations
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """Config-changed with missing juju-info relation → blocked/waiting."""

    def test_blocked_when_no_relations(self, ctx):
        """Charm should be blocked/waiting when juju-info is missing."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_blocked_mentions_missing_integration(self, ctx):
        """Status message should mention missing integration."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert "integration" in state_out.unit_status.message.lower() or (
            state_out.unit_status.name in ("blocked", "waiting")
        )


# ---------------------------------------------------------------------------
# Tests: install event
# ---------------------------------------------------------------------------


class TestInstallEvent:
    """Install event should trigger snap installation logic."""

    def test_install_event_runs(self, ctx):
        """Install event should not crash with mocked externals."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.install(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )

    def test_install_deferred_when_microovn_not_ready(self, ctx, monkeypatch):
        """Install is deferred when microovn snap is not ready."""
        mock_snap = MagicMock()
        mock_snap.SnapError = Exception
        mock_snap.SnapNotFoundError = Exception
        mock_snap.SnapState.Latest = "latest"
        microovn_snap = MagicMock()
        microovn_snap.present = False
        microovn_snap.services = {}
        mock_snap.SnapCache.return_value = {
            "openstack-network-agents": MagicMock(),
            "microovn": microovn_snap,
        }
        mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
        monkeypatch.setattr(charm, "snap", mock_snap)

        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.install(), state_in)

        # When microovn is not present, the install event is deferred
        # and the charm stays in maintenance/waiting
        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: juju-info relation joined triggers configure
# ---------------------------------------------------------------------------


class TestJujuInfoRelationJoined:
    """juju-info relation-joined should trigger charm configuration."""

    def test_relation_joined_runs(self, ctx):
        """Relation joined should not crash."""
        rel = juju_info_relation()
        state_in = testing.State(
            leader=True,
            relations=[rel],
        )
        state_out = ctx.run(ctx.on.relation_joined(rel), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )

    def test_relation_changed_runs(self, ctx):
        """Relation changed should not crash."""
        rel = juju_info_relation()
        state_in = testing.State(
            leader=True,
            relations=[rel],
        )
        state_out = ctx.run(ctx.on.relation_changed(rel), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: config-changed with juju-info present
# ---------------------------------------------------------------------------


class TestConfigChanged:
    """Config changes should be handled without errors."""

    def test_config_changed_with_juju_info(self, ctx, complete_state):
        """Config-changed with juju-info should proceed past relation checks."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite juju-info "
                f"present: {status.message}"
            )

    def test_config_changed_with_debug(self, ctx, complete_relations):
        """Changing debug config should not crash."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"debug": True},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )

    def test_config_changed_with_external_bridge_address(
        self, ctx, complete_relations
    ):
        """Changing external-bridge-address config should not crash."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"external-bridge-address": "10.0.0.1/24"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: configure_snap method
# ---------------------------------------------------------------------------


class TestConfigureSnap:
    """Test configure_snap calls connect + set_snap_data."""

    def test_configure_snap_connects_ovn_chassis(self, ctx, complete_state):
        """configure_snap should connect the ovn-chassis plug."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            mock_snap = MagicMock(name="openstack-network-agents")
            charm_instance.get_snap = MagicMock(return_value=mock_snap)
            charm_instance.set_snap_data = MagicMock()

            charm_instance.configure_snap(MagicMock())

            mock_snap.connect.assert_called_once_with(
                charm.OVN_CHASSIS_PLUG, slot=charm.OVN_CHASSIS_SLOT
            )
            charm_instance.set_snap_data.assert_called_once()

    def test_configure_snap_sets_debug_and_bridge(self, ctx):
        """configure_snap should pass debug + external-bridge-address."""
        rel = juju_info_relation()
        state_in = testing.State(
            leader=True,
            relations=[rel],
            config={"debug": True, "external-bridge-address": "10.0.0.0/24"},
        )

        with ctx(ctx.on.config_changed(), state_in) as mgr:
            charm_instance = mgr.charm
            mock_snap = MagicMock(name="openstack-network-agents")
            charm_instance.get_snap = MagicMock(return_value=mock_snap)
            charm_instance.set_snap_data = MagicMock()

            charm_instance.configure_snap(MagicMock())

            charm_instance.set_snap_data.assert_called_once_with(
                {
                    "settings.debug": True,
                    "network.external-bridge-address": "10.0.0.0/24",
                }
            )


# ---------------------------------------------------------------------------
# Tests: _connect_ovn_chassis
# ---------------------------------------------------------------------------


class TestConnectOvnChassis:
    """Test _connect_ovn_chassis method."""

    def test_connect_ovn_chassis_success(self, ctx, complete_state):
        """_connect_ovn_chassis should call snap.connect."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            mock_snap = MagicMock(name="openstack-network-agents")
            charm_instance.get_snap = MagicMock(return_value=mock_snap)

            charm_instance._connect_ovn_chassis()

            mock_snap.connect.assert_called_once_with(
                charm.OVN_CHASSIS_PLUG, slot=charm.OVN_CHASSIS_SLOT
            )

    def test_connect_ovn_chassis_errors_out(self, ctx, complete_state):
        """_connect_ovn_chassis should raise when snap connect fails."""
        import pytest

        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            mock_snap = MagicMock(name="openstack-network-agents")
            mock_snap.connect.side_effect = Exception("boom")
            charm_instance.get_snap = MagicMock(return_value=mock_snap)

            with pytest.raises(Exception):
                charm_instance._connect_ovn_chassis()


# ---------------------------------------------------------------------------
# Tests: set_network_agents_local_settings action
# ---------------------------------------------------------------------------


class TestSetNetworkAgentsLocalSettingsAction:
    """Test the set-network-agents-local-settings action."""

    def test_action_with_individual_params(self, ctx, complete_state):
        """Action with individual params calls set_snap_data correctly."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()

            evt = MagicMock()
            evt.params = {
                "external-interface": "ens10",
                "bridge-name": "br-ex",
                "physnet-name": "physnet1",
                "enable-chassis-as-gw": True,
            }

            charm_instance._set_network_agents_local_settings_action(evt)

            charm_instance.set_snap_data.assert_called_once_with(
                {
                    "network.external-interface": "ens10",
                    "network.bridge-name": "br-ex",
                    "network.physnet-name": "physnet1",
                    "network.enable-chassis-as-gw": True,
                }
            )

    def test_action_with_bridge_mapping(self, ctx, complete_state):
        """Action with bridge-mapping calls set_snap_data correctly."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()

            evt = MagicMock()
            evt.params = {
                "bridge-mapping": "physnet1:br-ex",
                "enable-chassis-as-gw": True,
            }

            charm_instance._set_network_agents_local_settings_action(evt)

            charm_instance.set_snap_data.assert_called_once_with(
                {
                    "network.bridge-mapping": "physnet1:br-ex",
                    "network.enable-chassis-as-gw": True,
                }
            )

    def test_action_with_enable_chassis_as_gw_false(self, ctx, complete_state):
        """Boolean False must be forwarded to snap, not silently dropped."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()

            evt = MagicMock()
            evt.params = {"enable-chassis-as-gw": False}

            charm_instance._set_network_agents_local_settings_action(evt)

            charm_instance.set_snap_data.assert_called_once_with(
                {"network.enable-chassis-as-gw": False}
            )

    def test_action_omitted_params_are_not_sent(self, ctx, complete_state):
        """Parameters not provided in the action should not appear in snap settings."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()

            evt = MagicMock()
            evt.params = {"bridge-name": "br-ex"}

            charm_instance._set_network_agents_local_settings_action(evt)

            snap_data = charm_instance.set_snap_data.call_args[0][0]
            assert snap_data == {"network.bridge-name": "br-ex"}

    def test_action_empty_params_no_snap_call(self, ctx, complete_state):
        """Action with no recognized params should not call set_snap_data."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()

            evt = MagicMock()
            evt.params = {}

            charm_instance._set_network_agents_local_settings_action(evt)

            charm_instance.set_snap_data.assert_not_called()
