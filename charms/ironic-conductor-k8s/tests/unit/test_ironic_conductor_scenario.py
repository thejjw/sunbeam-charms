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

"""Scenario (ops.testing state-transition) tests for ironic-conductor-k8s."""

from pathlib import (
    Path,
)
from unittest import (
    mock,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_contains,
    assert_config_file_exists,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    k8s_container,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)


class TestAllRelations:
    """With all relations complete the charm reaches active and configures the service."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_file_written(self, ctx, complete_state):
        """All relations present → ironic.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "ironic-conductor", "/etc/ironic/ironic.conf"
        )

    def test_ironic_conf_contains_client_regions(self, ctx, complete_state):
        """ironic.conf renders region_name in peer client sections."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "ironic-conductor",
            "/etc/ironic/ironic.conf",
            [
                "[nova]",
                "region_name = region12",
                "[neutron]",
                "[glance]",
                "[swift]",
                "[cinder]",
                "[service_catalog]",
            ],
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the services."""
        container = complete_state.get_container("ironic-conductor")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("ironic-conductor")
        assert "ironic-conductor" in out_container.layers
        layer = out_container.layers["ironic-conductor"]
        services = layer.to_dict().get("services", {})
        assert "ironic-conductor" in services

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        execs = [
            testing.Exec(command_prefix=["a2dissite"], return_code=0),
            testing.Exec(command_prefix=["a2ensite"], return_code=0),
            testing.Exec(command_prefix=["sudo"], return_code=0),
        ]
        container = k8s_container(
            "ironic-conductor", can_connect=True, execs=execs
        )
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked."""
        container = k8s_container("ironic-conductor", can_connect=False)
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

    @pytest.mark.parametrize(
        "missing_rel",
        sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_relation_missing(
        self, ctx, complete_relations, complete_secrets, container, missing_rel
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=[container],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(self, ctx, complete_secrets, container):
        """Non-leader unit waits for leader to bootstrap."""
        peer = testing.PeerRelation(
            endpoint="peers",
            local_app_data={
                "temp_url_secret": "fake-temp-url-secret",
            },
        )
        from ops_sunbeam.test_utils_scenario import (
            amqp_relation_complete,
            db_relation_complete,
            identity_credentials_relation_complete,
        )

        ceph_rgw = testing.Relation(
            endpoint="ceph-rgw-ready",
            remote_app_name="microceph",
            remote_app_data={"ready": "true"},
            remote_units_data={0: {}},
        )
        relations = [
            db_relation_complete(),
            amqp_relation_complete(),
            identity_credentials_relation_complete(),
            peer,
            ceph_rgw,
        ]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=[container],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message


class TestLoadbalancerIp:
    """Charm should be blocked when loadbalancer IP is unavailable."""

    def test_blocked_without_loadbalancer_ip(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """All relations present but no loadbalancer IP → blocked."""
        with mock.patch(
            "ops_sunbeam.k8s_resource_handlers.Client"
        ) as mock_client:
            client = mock_client.return_value
            svc = client.get.return_value
            svc.status.loadBalancer.ingress = []

            state_in = testing.State(
                leader=True,
                relations=complete_relations,
                containers=[container],
                secrets=complete_secrets,
            )
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "loadbalancer" in state_out.unit_status.message.lower()


class TestInvalidConfig:
    """Invalid configuration values → blocked."""

    def test_invalid_default_network_interface(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """default-network-interface not in enabled list → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={"default-network-interface": "foo"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_invalid_enabled_network_interfaces(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Invalid enabled-network-interfaces → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={
                "default-network-interface": "flat",
                "enabled-network-interfaces": "foo",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_invalid_enabled_hw_types(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Unsupported enabled-hw-types → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={
                "enabled-network-interfaces": "flat",
                "enabled-hw-types": "foo",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_valid_config_after_fix(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Valid hw-types after fixing config → active."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={"enabled-hw-types": "ipmi"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestCharmConfiguration:
    """Config context renders expected values in ironic.conf."""

    def test_default_hw_types_config(self, ctx, complete_state):
        """Default config (ipmi) renders expected hardware type settings."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        expected = [
            "enabled_bios_interfaces = no-bios",
            "enabled_boot_interfaces = pxe",
            "enabled_console_interfaces = ipmitool-shellinabox, ipmitool-socat, no-console",
            "enabled_deploy_interfaces = direct",
            "enabled_hardware_types = intel-ipmi, ipmi",
            "enabled_inspect_interfaces = no-inspect",
            "enabled_management_interfaces = intel-ipmitool, ipmitool, noop",
            "enabled_power_interfaces = ipmitool",
            "enabled_raid_interfaces = no-raid",
            "enabled_vendor_interfaces = ipmitool, no-vendor",
            "[hardware_type:intel-ipmi]",
            "[hardware_type:ipmi]",
            "default_deploy_interface = direct",
            "http_url=http://10.0.0.100:8080",
            "tftp_server = 10.0.0.100",
        ]
        assert_config_file_contains(
            state_out,
            ctx,
            "ironic-conductor",
            "/etc/ironic/ironic.conf",
            expected,
        )

    def test_multiple_hw_types_config(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Multiple hw types (fake,ipmi,redfish,idrac) render all expected interfaces."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={"enabled-hw-types": "fake,ipmi,redfish,idrac"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        expected = [
            "enabled_bios_interfaces = fake, idrac-wsman, no-bios",
            "enabled_boot_interfaces = fake, pxe, redfish-virtual-media",
            "enabled_console_interfaces = fake, ipmitool-shellinabox, ipmitool-socat, no-console",
            "enabled_deploy_interfaces = direct, fake",
            "enabled_hardware_types = fake-hardware, idrac, intel-ipmi, ipmi, redfish",
            "enabled_inspect_interfaces = fake, idrac-redfish, redfish, no-inspect",
            "enabled_management_interfaces = fake, idrac-redfish, intel-ipmitool, ipmitool, redfish, noop",
            "enabled_power_interfaces = fake, idrac-redfish, ipmitool, redfish",
            "enabled_raid_interfaces = fake, idrac-wsman, no-raid",
            "enabled_vendor_interfaces = fake, idrac-wsman, ipmitool, no-vendor",
            "[hardware_type:fake-hardware]",
            "default_deploy_interface = fake",
            "[hardware_type:idrac]",
            "[hardware_type:intel-ipmi]",
            "[hardware_type:ipmi]",
            "[hardware_type:redfish]",
        ]
        assert_config_file_contains(
            state_out,
            ctx,
            "ironic-conductor",
            "/etc/ironic/ironic.conf",
            expected,
        )

    def test_network_and_secret_config(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Cleaning/provisioning network and temp_url_secret render correctly."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            config={
                "cleaning-network": "foo",
                "provisioning-network": "lish",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        expected = [
            "cleaning_network = foo",
            "provisioning_network = lish",
            "swift_temp_url_key = fake-temp-url-secret",
            "swift_temp_url_duration = 1200",
        ]
        assert_config_file_contains(
            state_out,
            ctx,
            "ironic-conductor",
            "/etc/ironic/ironic.conf",
            expected,
        )


class TestSetTempUrlSecretAction:
    """Tests for the set-temp-url-secret action."""

    def test_action_fails_not_leader(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Non-leader unit → action fails."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), state_in)
        assert "action must be run on the leader unit." in str(exc_info.value)

    def test_action_fails_missing_relations(self, ctx, container):
        """Leader but no relations → action fails."""
        state_in = testing.State(leader=True, containers=[container])
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), state_in)
        assert "required relations are not yet available" in str(
            exc_info.value
        )

    @mock.patch("api_utils.create_keystone_session")
    def test_action_fails_keystone_session(
        self, mock_create_ks_session, ctx, complete_state
    ):
        """Keystone session creation fails → action fails."""
        mock_create_ks_session.side_effect = Exception("to be expected.")
        # Use a peer relation without temp_url_secret so configure_charm
        # doesn't interfere (action sets its own secret)
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)
        assert "failed to create keystone session" in str(exc_info.value)

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_action_fails_swift_not_available(
        self, mock_create_ks_session, mock_osclients, ctx, complete_state
    ):
        """Swift endpoint not found → action fails."""
        mock_osclients.return_value.has_swift.return_value = False
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)
        assert "Swift not yet available." in str(exc_info.value)

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_action_fails_glance_not_available(
        self, mock_create_ks_session, mock_osclients, ctx, complete_state
    ):
        """Glance endpoint not found → action fails."""
        os_cli = mock_osclients.return_value
        os_cli.has_swift.return_value = True
        os_cli.has_glance.return_value = False
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)
        assert "Glance not yet available." in str(exc_info.value)

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_action_fails_no_swift_backend(
        self, mock_create_ks_session, mock_osclients, ctx, complete_state
    ):
        """Glance has no swift store → action fails."""
        os_cli = mock_osclients.return_value
        os_cli.has_swift.return_value = True
        os_cli.has_glance.return_value = True
        os_cli.glance_stores = ["file"]
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)
        assert "Glance does not support Swift storage backend." in str(
            exc_info.value
        )

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_action_success_creates_secret(
        self, mock_create_ks_session, mock_osclients, ctx, complete_state
    ):
        """Successful action sets temp URL secret."""
        os_cli = mock_osclients.return_value
        os_cli.has_swift.return_value = True
        os_cli.has_glance.return_value = True
        os_cli.glance_stores = ["swift"]
        os_cli.get_object_account_properties.return_value = {}

        ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)

        assert ctx.action_results["output"] == "Temp URL secret set."
        os_cli.set_object_account_property.assert_called_once()

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_action_success_secret_already_exists(
        self, mock_create_ks_session, mock_osclients, ctx, complete_state
    ):
        """Action succeeds without setting secret when it already exists in swift."""
        os_cli = mock_osclients.return_value
        os_cli.has_swift.return_value = True
        os_cli.has_glance.return_value = True
        os_cli.glance_stores = ["swift"]
        # Return matching secret from swift
        os_cli.get_object_account_properties.return_value = {
            "temp-url-key": "fake-temp-url-secret",
        }

        ctx.run(ctx.on.action("set-temp-url-secret"), complete_state)

        assert ctx.action_results["output"] == "Temp URL secret set."
        os_cli.set_object_account_property.assert_not_called()


class TestContainerDisconnectBlocksOrWaits:
    """Config-changed with disconnected containers → blocked/waiting."""

    def test_container_disconnect(self, ctx, complete_state):
        """Charm should block/wait when containers cannot connect."""
        assert_container_disconnect_causes_waiting_or_blocked(
            ctx, complete_state
        )


class TestRelationBrokenBlocksOrWaits:
    """Breaking each mandatory relation → blocked/waiting."""

    @pytest.mark.parametrize(
        "relation_endpoint",
        sorted(MANDATORY_RELATIONS),
    )
    def test_relation_broken(self, ctx, complete_state, relation_endpoint):
        """Charm should block/wait when a mandatory relation is broken."""
        assert_relation_broken_causes_blocked_or_waiting(
            ctx, complete_state, relation_endpoint
        )
