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

"""Scenario (ops.testing state-transition) tests for octavia-k8s."""

from pathlib import (
    Path,
)
from unittest import (
    mock,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_contains,
    assert_config_file_exists,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    k8s_api_container,
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
        """All relations present → octavia.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "octavia-api", "/etc/octavia/octavia.conf"
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready on octavia-api adds a layer and starts the WSGI service."""
        container = complete_state.get_container("octavia-api")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("octavia-api")
        assert "octavia-api" in out_container.layers
        layer = out_container.layers["octavia-api"]
        assert "wsgi-octavia-api" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("wsgi-octavia-api") == (
            testing.pebble.ServiceStatus.ACTIVE
        )


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        containers = [
            k8s_api_container("octavia-api", can_connect=False),
            k8s_container("octavia-driver-agent", can_connect=False),
            k8s_container("octavia-housekeeping", can_connect=False),
            k8s_container("octavia-health-manager", can_connect=False),
            k8s_container("octavia-worker", can_connect=False),
        ]
        state_in = testing.State(leader=True, containers=containers)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

    @pytest.mark.parametrize(
        "missing_rel",
        sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_relation_missing(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        missing_rel,
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=all_containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message


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


class TestAmphoraNetworkAttachment:
    """Tests covering behaviour gated on the amphora-network-attachment config.

    These tests verify:
    - Status messages when the management interface is not yet detected.
    - Certificate paths written to octavia.conf when Amphora is enabled.
    - The [health_manager] section rendered when peer unit data has a bind IP.
    - The barbican cert_manager line rendered when barbican is ready.
    - Amphora containers started / stopped based on the config flag.
    """

    def test_waiting_when_lbmgmt_interface_not_detected(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """Amphora enabled but second interface absent → WaitingStatus."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name == "waiting"
        assert "interface" in state_out.unit_status.message.lower()

    def test_config_contains_cert_paths_when_amphora_enabled(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """Amphora certs in config → octavia.conf has cert file paths."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "ca_certificate = /etc/octavia/certs/issuing_ca.pem",
                "ca_private_key = /etc/octavia/certs/issuing_ca_key.pem",
                "ca_private_key_passphrase = s3cr3t-passphrase",
                "server_ca = /etc/octavia/certs/issuing_ca.pem",
                "client_ca = /etc/octavia/certs/controller_ca.pem",
                "client_cert = /etc/octavia/certs/controller_cert.pem",
            ],
        )

    def test_config_uses_barbican_cert_manager_when_ready(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """barbican-service ready → cert_manager = barbican_cert_manager written."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["cert_manager = barbican_cert_manager"],
        )

    def test_config_contains_health_manager_bind_ip(
        self,
        ctx,
        complete_relations,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """lbmgmt-ip pre-seeded in peer data → [health_manager] bind_ip rendered."""
        # Replace the plain peer relation with one pre-seeded with lbmgmt-ip
        # so that AmphoraHealthManagerContext can read it during config rendering.
        peer_with_ip = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "192.168.100.10"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_with_ip]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "[health_manager]",
                "bind_ip = 192.168.100.10",
            ],
        )

    def test_amphora_containers_stopped_without_amphora_config(
        self,
        ctx,
        complete_state,
    ):
        """Without amphora-network-attachment health-manager and worker are not started."""
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")
        hm_container = state_out.get_container("octavia-health-manager")
        worker_container = state_out.get_container("octavia-worker")
        hm_status = hm_container.service_statuses.get("octavia-health-manager")
        wk_status = worker_container.service_statuses.get("octavia-worker")
        # init_service skips start_service when Amphora not configured, so
        # the services are never started and do not appear in service_statuses.
        assert hm_status is None
        assert wk_status is None

    def test_amphora_containers_started_with_amphora_config(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """With amphora-network-attachment the health-manager and worker run."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_second_interface_ip",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        hm_container = state_out.get_container("octavia-health-manager")
        worker_container = state_out.get_container("octavia-worker")
        hm_status = hm_container.service_statuses.get("octavia-health-manager")
        wk_status = worker_container.service_statuses.get("octavia-worker")
        assert hm_status == testing.pebble.ServiceStatus.ACTIVE
        assert wk_status == testing.pebble.ServiceStatus.ACTIVE


class TestAmphoraConfig:
    """Tests for Amphora-specific configuration and container lifecycle."""

    def test_blocked_when_amphora_certs_missing(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """Setting amphora-network-attachment without certs → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert (
            "Amphora certificates not configured"
            in state_out.unit_status.message
        )

    def test_blocked_when_barbican_not_integrated(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """amphora-network-attachment set with certs but no barbican relation → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={
                "amphora-network-attachment": "octavia-mgmt",
                "lb-mgmt-issuing-cacert": "dGVzdA==",
                "lb-mgmt-issuing-ca-private-key": "dGVzdA==",
                "lb-mgmt-issuing-ca-key-passphrase": "passphrase",
                "lb-mgmt-controller-cacert": "dGVzdA==",
                "lb-mgmt-controller-cert": "dGVzdA==",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "barbican-service" in state_out.unit_status.message

    def test_waiting_when_barbican_not_ready(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """barbican-service integrated but not yet ready → WaitingStatus."""
        barbican_rel = testing.Relation(
            endpoint="barbican-service",
            remote_app_name="barbican-k8s",
            remote_app_data={"ready": "false"},
        )
        state_in = testing.State(
            leader=True,
            relations=complete_relations + [barbican_rel],
            containers=all_containers,
            secrets=complete_secrets,
            config={
                "amphora-network-attachment": "octavia-mgmt",
                "lb-mgmt-issuing-cacert": "dGVzdA==",
                "lb-mgmt-issuing-ca-private-key": "dGVzdA==",
                "lb-mgmt-issuing-ca-key-passphrase": "passphrase",
                "lb-mgmt-controller-cacert": "dGVzdA==",
                "lb-mgmt-controller-cert": "dGVzdA==",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "waiting"
        assert "barbican" in state_out.unit_status.message.lower()
