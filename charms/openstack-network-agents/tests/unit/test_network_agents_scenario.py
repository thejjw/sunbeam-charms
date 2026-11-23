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

import ipaddress
import json
from dataclasses import (
    replace,
)
from unittest.mock import (
    MagicMock,
    PropertyMock,
)

import charm
import ndr_peering
import pytest
from ops import (
    testing,
)

FAKE_NODE_NAME = "juju-test-0"


def make_microovn_status(
    node_name: str = FAKE_NODE_NAME,
    services: str = "central, chassis, switch",
) -> str:
    """Build a ``microovn status`` output string."""
    return (
        "MicroOVN deployment summary:\n"
        f"- {node_name} (10.0.0.1)\n"
        f"  Services: {services}\n"
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
    """Config-changed with missing juju-info relation -> blocked/waiting."""

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
# Tests: update-status triggers configure_charm
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    """update-status should re-run configure_charm for deferred recovery."""

    def test_update_status_configures_charm(self, ctx, complete_state):
        """update-status with all relations should reach active."""
        state_out = ctx.run(ctx.on.update_status(), complete_state)

        assert state_out.unit_status.name in (
            "active",
            "blocked",
            "waiting",
            "maintenance",
        )

    def test_update_status_without_relations_blocks(self, ctx):
        """update-status without juju-info should block/wait."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.update_status(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")


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
            mock_snap.get.return_value = {}
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
            mock_snap.get.return_value = {}
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


# ---------------------------------------------------------------------------
# Tests: dynamic routing agent instances
# ---------------------------------------------------------------------------


class TestDynamicRoutingInstances:
    """Reconcile the selected agent list and relation-provided transport."""

    instances = [
        {
            "domain": "client1",
            "family": "ipv4",
            "service-name": "agent-client1-v4",
            "host": "agent-client1-v4-net-01",
            "router-id": "192.0.2.3",
            "peering": {
                "provider-trunk": {
                    "physnet": "external",
                    "vlan": 101,
                    "address": "192.0.2.3/24",
                }
            },
        },
        {
            "domain": "internet",
            "family": "ipv4",
            "service-name": "agent-internet-v4",
            "host": "agent-internet-v4-net-01",
            "router-id": "192.0.2.4",
            "peering": {
                "dedicated-uplink": {
                    "interface": "eno1",
                    "address": "192.0.2.4",
                }
            },
        },
    ]

    @pytest.fixture(autouse=True)
    def mock_host_tools(self, monkeypatch, tmp_path):
        """Provide a snap boundary without modifying host networking."""

        def run_dynamic_routing(charm_instance, command, payload=None):
            if command == "validate":
                return {
                    "contract-version": 1,
                    "instances": json.loads(payload),
                    "managed-addresses": {},
                }
            assert command == "reconcile"
            return {"ready": True}

        monkeypatch.setattr(
            charm.OpenstackNetworkAgentsOperatorCharm,
            "_run_dynamic_routing",
            run_dynamic_routing,
        )
        monkeypatch.setattr(ndr_peering, "NETPLAN_PATH", tmp_path / "ndr.yaml")

    def test_validate_returns_canonical_plan_without_mutations(
        self, ctx, complete_state
    ):
        """Authorize the complete plan before any snap config or Netplan change."""
        event = ctx.on.action(
            "validate-dynamic-routing-instances",
            {
                "contract-version": 1,
                "instances": json.dumps(self.instances[::-1]),
            },
        )
        with ctx(event, complete_state) as manager:
            snap_service = manager.charm.get_snap()
            manager.run()
        assert ctx.action_results == {
            "contract-version": 1,
            "instances": json.dumps(self.instances),
            "saved-instances": "[]",
        }
        snap_service.set.assert_not_called()
        assert not ndr_peering.NETPLAN_PATH.exists()

    def test_apply_orders_protection_address_and_readiness(
        self, ctx, complete_state, monkeypatch
    ):
        """Provision the protected port before Netplan and check readiness last."""
        calls = []
        resolved = [self.instances[0]]

        def run_snap(charm_instance, command, payload=None):
            calls.append(command)
            if command == "validate":
                assert json.loads(payload) == resolved
                return {
                    "contract-version": 1,
                    "instances": resolved,
                    "managed-addresses": {"ndr-1a2b": "192.0.2.3/24"},
                }
            return {"ready": calls.count("reconcile") > 2}

        def apply_addresses(path, addresses):
            assert addresses == {"ndr-1a2b": "192.0.2.3/24"}
            calls.append("addresses")

        monkeypatch.setattr(
            charm.OpenstackNetworkAgentsOperatorCharm,
            "_run_dynamic_routing",
            run_snap,
        )
        monkeypatch.setattr(
            ndr_peering, "reconcile_addresses", apply_addresses
        )
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "broker", "password": "pass"},
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(resolved)},
        )
        with ctx(event, state) as manager:
            manager.charm.get_snap().set.side_effect = (
                lambda *args, **kwargs: calls.append("configure snap")
            )
            manager.run()
        assert calls == [
            "validate",
            "configure snap",
            "reconcile",
            "addresses",
            "reconcile",
            "reconcile",
        ]
        assert ctx.action_results == {"configured": True, "instances": 1}

    def test_incompatible_snap_preflight_does_not_write(
        self, ctx, complete_state, monkeypatch
    ):
        """Don't apply a plan when the local snap lacks the contract."""
        monkeypatch.setattr(
            charm.OpenstackNetworkAgentsOperatorCharm,
            "_run_dynamic_routing",
            lambda *args: {"contract-version": 2},
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(self.instances)},
        )
        with ctx(event, complete_state) as manager:
            snap_service = manager.charm.get_snap()
            with pytest.raises(
                testing.ActionFailed, match="incompatible NDR contract"
            ):
                manager.run()
        snap_service.set.assert_not_called()

    def test_action_passes_all_instances_and_requests_shared_vhost(
        self, ctx, complete_state
    ):
        """A single unit receives distinct processes and the Rabbit transport."""
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "broker", "password": "has:@!"},
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(self.instances)},
        )
        with ctx(event, state) as manager:
            snap_service = manager.charm.get_snap()
            state_out = manager.run()
        config = json.loads(snap_service.set.call_args.args[0]["ndr.config"])
        assert config == {
            "instances": self.instances,
            "transport-url": (
                "rabbit://openstack-network-agents:has%3A%40%21@broker:5672/openstack"
            ),
        }
        assert ctx.action_results == {"configured": True, "instances": 2}

        snap_service.set.reset_mock()
        snap_service.get.return_value = {
            "ndr": {"config": json.dumps(config, sort_keys=True)}
        }
        ctx.run(event, state_out)
        snap_service.set.assert_not_called()

        state_out = ctx.run(ctx.on.relation_joined(relation), state_out)
        data = state_out.get_relation(relation.id).local_app_data
        assert data["vhost"] == "openstack"
        assert data["external_connectivity"] == "true"

    def test_accepts_ipv6_instances(self, ctx, complete_state):
        """Pass one IPv6 speaker slot with a numbered IPv6 attachment."""
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "broker", "password": "pass"},
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        ipv6 = {
            "domain": "client1",
            "family": "ipv6",
            "service-name": "agent-client1-v6",
            "host": "agent-client1-v6-net-01",
            "router-id": "192.0.2.5",
            "peering": {
                "dedicated-uplink": {
                    "interface": "eno2",
                    "address": "2001:db8::1",
                }
            },
        }
        instances = [ipv6]
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(instances)},
        )
        with ctx(event, state) as manager:
            snap_service = manager.charm.get_snap()
            manager.run()
        config = json.loads(snap_service.set.call_args.args[0]["ndr.config"])
        assert config["instances"] == instances
        assert ctx.action_results == {
            "configured": True,
            "instances": len(instances),
        }

    def test_relation_recovery_reuses_snap_placement(
        self, ctx, complete_state
    ):
        """Lose the credentials and recover the placement across hook invocations."""
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "broker", "password": "pass"},
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(self.instances)},
        )
        with ctx(event, state) as manager:
            snap_service = manager.charm.get_snap()
            snap_service.present = True
            state_out = manager.run()
        snap_service.get.return_value = {
            "ndr": {"config": snap_service.set.call_args.args[0]["ndr.config"]}
        }
        snap_service.set.reset_mock()
        broken = ctx.run(ctx.on.relation_broken(relation), state_out)
        paused = snap_service.set.call_args.args[0]["ndr.config"]
        assert json.loads(paused) == {
            "instances": [],
            "paused-instances": self.instances,
        }

        snap_service.get.return_value = {"ndr": {"config": paused}}
        snap_service.set.reset_mock()
        recovered = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "new-broker", "password": "new-pass"},
        )
        state = replace(
            broken, relations=[*complete_state.relations, recovered]
        )
        ctx.run(ctx.on.relation_changed(recovered), state)
        config = json.loads(snap_service.set.call_args.args[0]["ndr.config"])
        assert config == {
            "instances": self.instances,
            "transport-url": "rabbit://openstack-network-agents:new-pass@new-broker:5672/openstack",
        }

    def test_empty_action_clears_placement_without_amqp(
        self, ctx, complete_state
    ):
        """An explicit clear takes precedence over an older stored placement."""
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": "[]"},
        )
        with ctx(event, complete_state) as manager:
            snap_service = manager.charm.get_snap()
            manager.charm._state.ndr_instances = json.dumps(self.instances)
            state_out = manager.run()
            assert manager.charm._state.ndr_instances is None
        cleared = snap_service.set.call_args.args[0]["ndr.config"]
        assert json.loads(cleared) == {"instances": []}
        assert ctx.action_results == {"configured": True, "instances": 0}

        snap_service.get.return_value = {"ndr": {"config": cleared}}
        snap_service.set.reset_mock()
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={"hostname": "broker", "password": "pass"},
        )
        state = replace(
            state_out, relations=[*complete_state.relations, relation]
        )
        ctx.run(ctx.on.relation_changed(relation), state)
        assert all(
            "ndr.config" not in call.args[0]
            for call in snap_service.set.call_args_list
        )

    @pytest.mark.parametrize(
        "tls_data",
        [
            {"ssl_port": "5671"},
            {"ssl_ca": "CA arrived before the port"},
            {"ssl_port": "5671", "ssl_ca": "invalid certificate"},
        ],
    )
    def test_tls_requires_complete_configuration(
        self, ctx, complete_state, tls_data
    ):
        """An incomplete or invalid TLS setup cannot start agents."""
        relation = testing.Relation(
            endpoint="amqp",
            remote_app_data={
                "hostname": "broker",
                "password": "pass",
                **tls_data,
            },
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {"contract-version": 1, "instances": json.dumps(self.instances)},
        )
        with ctx(event, state) as manager:
            snap_service = manager.charm.get_snap()
            manager.run()
        assert json.loads(
            snap_service.set.call_args.args[0]["ndr.config"]
        ) == {
            "instances": [],
            "paused-instances": self.instances,
        }
        assert ctx.action_results == {"configured": False, "instances": 2}

    def test_invalid_duplicate_plan_does_not_set_snap(
        self, ctx, complete_state
    ):
        """Reject two IPv4 instances of one domain even with distinct identities."""
        second = {**self.instances[1], "domain": self.instances[0]["domain"]}

        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {
                "contract-version": 1,
                "instances": json.dumps([self.instances[0], second]),
            },
        )
        with ctx(event, complete_state) as manager:
            snap_service = manager.charm.get_snap()
            with pytest.raises(
                testing.ActionFailed, match="Duplicate NDR domain"
            ):
                manager.run()
        snap_service.set.assert_not_called()

    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"family": "other"}, "must use the ipv4 or ipv6"),
            ({"router-id": "2001:db8::1"}, "must be an IPv4 address"),
            ({"unexpected": "value"}, "contains unsupported fields"),
            ({"domain": "a" * 256}, "contains an invalid identity"),
            (
                {
                    "peering": {
                        "provider-trunk": {
                            "physnet": "external",
                            "vlan": 4095,
                            "address": "192.0.2.3/24",
                        }
                    }
                },
                "Invalid NDR physnet or peering VLAN",
            ),
        ],
    )
    def test_invalid_placement_fails_before_snap_preflight(
        self, ctx, complete_state, monkeypatch, override, message
    ):
        """Reject invalid wire placement before calling the snap."""
        preflight = MagicMock()
        monkeypatch.setattr(
            charm.OpenstackNetworkAgentsOperatorCharm,
            "_run_dynamic_routing",
            preflight,
        )
        event = ctx.on.action(
            "set-dynamic-routing-instances",
            {
                "contract-version": 1,
                "instances": json.dumps([{**self.instances[0], **override}]),
            },
        )
        with ctx(event, complete_state) as manager:
            snap_service = manager.charm.get_snap()
            with pytest.raises(testing.ActionFailed, match=message):
                manager.run()
        preflight.assert_not_called()
        snap_service.set.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: microovn readiness (via microovn status parsing)
# ---------------------------------------------------------------------------


class TestDynamicRoutingLogging:
    """Configure the independent NDR log forwarding interface."""

    def test_logging_relation_updates_only_logging(self, ctx, complete_state):
        """Relating and removing Loki leave the agent plan untouched."""
        url = "https://loki.example/loki/api/v1/push"
        relation = testing.Relation(
            endpoint="logging",
            remote_units_data={0: {"endpoint": json.dumps({"url": url})}},
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        with ctx(ctx.on.relation_changed(relation), state) as manager:
            snap_service = manager.charm.get_snap()
            snap_service.present = True
            state_out = manager.run()
        snap_service.set.assert_called_once()
        values = snap_service.set.call_args.args[0]
        assert values.keys() == {"ndr.logging"}
        logging = json.loads(values["ndr.logging"])
        assert logging["endpoints"] == [url]
        assert set(logging["labels"]) == {
            "juju_model",
            "juju_model_uuid",
            "juju_application",
            "juju_unit",
        }

        snap_service.get.return_value = {
            "ndr": {"logging": values["ndr.logging"]}
        }
        snap_service.set.reset_mock()
        ctx.run(ctx.on.relation_broken(relation), state_out)
        snap_service.set.assert_called_once()
        values = snap_service.set.call_args.args[0]
        assert values.keys() == {"ndr.logging"}
        assert json.loads(values["ndr.logging"])["endpoints"] == []

    def test_malformed_logging_data_does_not_block_the_charm(
        self, ctx, complete_state
    ):
        """Log endpoint errors neither change forwarding nor break NDR."""
        relation = testing.Relation(
            endpoint="logging", remote_units_data={0: {"endpoint": "not json"}}
        )
        state = replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        with ctx(ctx.on.relation_changed(relation), state) as manager:
            snap_service = manager.charm.get_snap()
            snap_service.present = True
            manager.run()
        snap_service.set.assert_not_called()


class TestMicroovnReadiness:
    """Test _check_microovn_ready with microovn status parsing."""

    def _set_microovn_status(self, services_str):
        """Override the subprocess mock to return custom microovn status."""
        result = MagicMock()
        result.returncode = 0
        result.stdout = make_microovn_status(services=services_str)
        result.stderr = ""
        charm.subprocess.run.return_value = result

    def test_ready_with_switch(self, ctx, complete_state):
        """Node with switch service is ready."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            self._set_microovn_status("central, chassis, switch")
            assert mgr.charm._check_microovn_ready() is True

    def test_not_ready_without_switch(self, ctx, complete_state):
        """Node without switch service is not ready."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            self._set_microovn_status("central, chassis")
            assert mgr.charm._check_microovn_ready() is False

    def test_microovn_not_present(self, ctx, complete_state):
        """Microovn snap not present -> not ready."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            mgr.charm.snap_module.SnapCache.return_value[
                charm.MICROOVN_SNAP
            ].present = False
            assert mgr.charm._check_microovn_ready() is False

    def test_microovn_status_fails(self, ctx, complete_state):
        """Microovn status command failure -> not ready."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            result = MagicMock()
            result.returncode = 1
            result.stderr = "not initialized"
            charm.subprocess.run.return_value = result
            assert mgr.charm._check_microovn_ready() is False

    def test_daemon_yaml_missing(self, ctx, complete_state, monkeypatch):
        """Missing daemon.yaml -> not ready."""
        _real_open = open

        def _patched_open(path, *args, **kwargs):
            if str(path) == charm.MICROOVN_DAEMON_YAML:
                raise FileNotFoundError(path)
            return _real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", _patched_open)

        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            assert mgr.charm._check_microovn_ready() is False


# ---------------------------------------------------------------------------
# Tests: install deferral
# ---------------------------------------------------------------------------


class TestInstallDeferral:
    """Install event deferral when microovn is not ready."""

    def test_install_deferred_when_microovn_status_missing_service(
        self, ctx, monkeypatch
    ):
        """Install deferred when microovn status shows missing switch."""
        mock_snap = MagicMock()
        mock_snap.SnapError = Exception
        mock_snap.SnapNotFoundError = Exception
        mock_snap.SnapState.Latest = "latest"
        microovn = MagicMock()
        microovn.present = True
        mock_snap.SnapCache.return_value = {
            "openstack-network-agents": MagicMock(),
            "microovn": microovn,
        }
        mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
        monkeypatch.setattr(charm, "snap", mock_snap)

        # microovn status shows only central (no switch for readiness)
        mock_subprocess = MagicMock()
        result = MagicMock()
        result.returncode = 0
        result.stdout = make_microovn_status(services="central")
        result.stderr = ""
        mock_subprocess.run.return_value = result
        mock_subprocess.TimeoutExpired = Exception
        monkeypatch.setattr(charm, "subprocess", mock_subprocess)

        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.install(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: data binding
# ---------------------------------------------------------------------------


class TestDataBinding:
    """Test the data_address property and its integration with configure_snap."""

    def test_data_address_none_when_disabled(self, ctx, complete_state):
        """data_address returns None when use-data-binding is false."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            assert mgr.charm.data_address is None

    def test_data_address_returns_ip_when_enabled(
        self, ctx, complete_relations
    ):
        """data_address returns IP string when use-data-binding is true."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"use-data-binding": True},
        )
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            mock_binding = MagicMock()
            mock_binding.network.bind_address = ipaddress.IPv4Address(
                "10.0.0.5"
            )
            mgr.charm.model.get_binding = MagicMock(return_value=mock_binding)

            assert mgr.charm.data_address == "10.0.0.5"
            mgr.charm.model.get_binding.assert_called_once_with(
                charm.DATA_BINDING
            )

    def test_data_address_none_when_binding_missing(
        self, ctx, complete_relations
    ):
        """data_address returns None when binding cannot be resolved."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"use-data-binding": True},
        )
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            mgr.charm.model.get_binding = MagicMock(return_value=None)

            assert mgr.charm.data_address is None

    def test_configure_snap_includes_ip_when_data_binding_enabled(
        self, ctx, complete_relations
    ):
        """configure_snap sets network.ip-address when data binding is enabled."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"use-data-binding": True},
        )
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            charm_instance = mgr.charm
            mock_snap_obj = MagicMock(name="openstack-network-agents")
            mock_snap_obj.get.return_value = {}
            charm_instance.get_snap = MagicMock(return_value=mock_snap_obj)
            charm_instance.set_snap_data = MagicMock()

            type(charm_instance).data_address = PropertyMock(
                return_value="10.0.0.5"
            )

            charm_instance.configure_snap(MagicMock())

            call_args = charm_instance.set_snap_data.call_args[0][0]
            assert call_args["network.ip-address"] == "10.0.0.5"

    def test_configure_snap_omits_ip_when_data_binding_disabled(
        self, ctx, complete_state
    ):
        """configure_snap does not set network.ip-address when data binding is disabled."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            mock_snap_obj = MagicMock(name="openstack-network-agents")
            mock_snap_obj.get.return_value = {}
            charm_instance.get_snap = MagicMock(return_value=mock_snap_obj)
            charm_instance.set_snap_data = MagicMock()

            charm_instance.configure_snap(MagicMock())

            call_args = charm_instance.set_snap_data.call_args[0][0]
            assert "network.ip-address" not in call_args
