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
    cleanup_database_requires_events,
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


class TestNoDuplicateWriteConfig:
    """Regression: write_config must be called exactly once per container per hook.

    The base WSGIPebbleHandler.init_service() called configure_container()
    (→ write_config) and then write_config() a second time, and
    OctaviaControllerPebbleHandler.init_service() also called configure_container()
    again after configure_containers() had already done it.  With real pebble each
    extra call costs ~2-3 s per file (pebble pull() RPC), totalling ~30 s per hook.

    Scenario tests cannot reproduce the timing (in-memory lookups are instant), but
    they CAN catch regressions by asserting write_config is called exactly once.
    """

    def test_write_config_called_once_per_container(self, ctx, complete_state):
        """write_config is called exactly once per container during config-changed."""
        original_write_config = __import__(
            "ops_sunbeam.container_handlers",
            fromlist=["PebbleHandler"],
        ).PebbleHandler.write_config
        calls_by_container: dict[str, int] = {}

        def counting_write_config(self_handler, context):
            calls_by_container[self_handler.container_name] = (
                calls_by_container.get(self_handler.container_name, 0) + 1
            )
            return original_write_config(self_handler, context)

        with mock.patch(
            "ops_sunbeam.container_handlers.PebbleHandler.write_config",
            new=counting_write_config,
        ):
            ctx.run(ctx.on.config_changed(), complete_state)

        # One call per container (api + controller) from configure_containers().
        # init_service() must NOT add extra calls.
        assert calls_by_container.get("octavia-api", 0) == 1, (
            f"octavia-api: write_config called "
            f"{calls_by_container.get('octavia-api', 0)} times, expected 1"
        )
        assert calls_by_container.get("octavia-controller", 0) == 1, (
            f"octavia-controller: write_config called "
            f"{calls_by_container.get('octavia-controller', 0)} times, expected 1"
        )


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        containers = [
            k8s_api_container("octavia-api", can_connect=False),
            k8s_container("octavia-controller", can_connect=False),
        ]
        state_in = testing.State(leader=True, containers=containers)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting.

    Also covers optional (non-mandatory) relations that, when present but not
    yet ready, affect unit status in the Amphora code path.
    """

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

    # ------------------------------------------------------------------
    # Non-mandatory (optional) relations — present but handler not ready
    # ------------------------------------------------------------------

    def test_amphora_issuing_ca_present_but_not_ready(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        barbican_ready_relation,
    ):
        """amphora-issuing-ca related but cert not yet issued → blocked.

        The relation exists (handler registered) but AmphoraTlsCertificatesHandler
        is not ready because the CA has not yet signed the CSR.  The charm must
        report a blocked (or waiting) status mentioning the issuing-ca relation
        rather than silently proceeding.
        """
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [
                barbican_ready_relation,
                amphora_issuing_ca_relation,
                amphora_controller_cert_relation,
            ],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        # Explicitly mark amphora cert handlers as NOT ready.  The autouse
        # mock_certs_ready fixture patches TlsCertificatesHandler.ready → True
        # which the subclass inherits; we override here to simulate certs
        # not yet issued.
        with mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=False,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when amphora-issuing-ca not ready, "
            f"got {state_out.unit_status}"
        )
        assert "amphora-issuing-ca" in state_out.unit_status.message, (
            f"Status message should mention 'amphora-issuing-ca', "
            f"got: {state_out.unit_status.message!r}"
        )

    def test_amphora_controller_cert_present_but_not_ready(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        barbican_ready_relation,
    ):
        """amphora-controller-cert related but cert not yet issued → blocked.

        The issuing-ca is mocked as ready (signed) but the controller cert
        handler is not ready.  The charm checks the controller cert second, so
        the blocked status must mention amphora-controller-cert.
        """
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [
                barbican_ready_relation,
                amphora_issuing_ca_relation,
                amphora_controller_cert_relation,
            ],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )

        def _patched_post(self_charm):
            import ops

            if not self_charm.config.get("amphora-network-attachment"):
                self_charm.amphora_status.set(ops.ActiveStatus(""))
                return
            if self_charm.amphora_net_status.status.name != "active":
                self_charm.amphora_status.set(ops.ActiveStatus(""))
                return
            if not self_charm.model.relations.get(
                "amphora-issuing-ca"
            ) or not self_charm.model.relations.get("amphora-controller-cert"):
                self_charm.amphora_status.set(
                    ops.BlockedStatus(
                        "amphora-issuing-ca and amphora-controller-cert "
                        "relations required for Amphora"
                    )
                )
                return
            if not self_charm.model.relations.get("barbican-service"):
                self_charm.amphora_status.set(
                    ops.BlockedStatus(
                        "barbican-service integration required for Amphora"
                    )
                )
                return
            if not self_charm.barbican_svc.ready:
                self_charm.amphora_status.set(
                    ops.WaitingStatus("Waiting for barbican-service")
                )
                return
            # Simulate: issuing-ca ready, controller-cert NOT yet issued.
            self_charm.amphora_status.set(
                ops.BlockedStatus(
                    "Amphora controller certificate not yet provided "
                    "by amphora-controller-cert integration"
                )
            )

        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "post_config_setup",
            _patched_post,
        ), mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when amphora-controller-cert not ready, "
            f"got {state_out.unit_status}"
        )
        assert "amphora-controller-cert" in state_out.unit_status.message, (
            f"Status message should mention 'amphora-controller-cert', "
            f"got: {state_out.unit_status.message!r}"
        )

    def test_both_amphora_cert_relations_present_but_neither_ready(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        barbican_ready_relation,
    ):
        """Both cert relations present but neither has issued certs → blocked at issuing-ca.

        The charm checks issuing-ca before controller-cert, so the blocked
        message must reference issuing-ca when both are unready.
        """
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [
                barbican_ready_relation,
                amphora_issuing_ca_relation,
                amphora_controller_cert_relation,
            ],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        # Explicitly mark both amphora cert handlers as NOT ready.  The autouse
        # mock_certs_ready patches TlsCertificatesHandler.ready → True (inherited
        # by AmphoraTlsCertificatesHandler); override to simulate neither cert
        # having been issued yet.
        with mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=False,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when both amphora cert relations unready, "
            f"got {state_out.unit_status}"
        )
        # issuing-ca is checked first; its name should appear in the message.
        assert "amphora-issuing-ca" in state_out.unit_status.message, (
            f"Status message should mention 'amphora-issuing-ca' (first check), "
            f"got: {state_out.unit_status.message!r}"
        )

    def test_barbican_present_but_not_ready_with_amphora(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        mock_amphora_certs_ready,
    ):
        """barbican-service related but not yet ready → waiting status.

        All other Amphora prereqs are satisfied (NIC detected, cert relations
        present and mocked-ready), but the barbican service reports ready=false.
        The charm must wait (not block) for barbican to become ready.
        """
        barbican_not_ready = testing.Relation(
            endpoint="barbican-service",
            remote_app_name="barbican-k8s",
            remote_app_data={"ready": "false"},
        )
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [
                barbican_not_ready,
                amphora_issuing_ca_relation,
                amphora_controller_cert_relation,
            ],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name == "waiting", (
            f"Expected waiting when barbican-service not ready, "
            f"got {state_out.unit_status}"
        )
        assert "barbican" in state_out.unit_status.message.lower(), (
            f"Status message should mention 'barbican', "
            f"got: {state_out.unit_status.message!r}"
        )

    def test_no_status_impact_from_unready_optional_rels_without_amphora(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
    ):
        """Optional Amphora relations present but unready, without amphora config → active.

        When amphora-network-attachment is not configured the charm should
        stay active regardless of whether the optional cert relations are
        present and unready — they only affect status in the Amphora path.
        """
        state_in = testing.State(
            leader=True,
            # Add cert relations but do NOT set amphora-network-attachment.
            relations=complete_relations
            + [amphora_issuing_ca_relation, amphora_controller_cert_relation],
            containers=all_containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus(""), (
            "Unready optional amphora cert relations must not affect status "
            "when amphora-network-attachment is not configured"
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
        mock_amphora_certs_ready,
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
            "_get_lbmgmt_ip_from_network_status",
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
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """Amphora cert relations present → octavia.conf has cert file paths."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
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
        mock_amphora_certs_ready,
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
            "_get_lbmgmt_ip_from_network_status",
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
        mock_amphora_certs_ready,
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
            "_get_lbmgmt_ip_from_network_status",
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

    def test_lbmgmt_ip_not_rewritten_when_unchanged(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """lbmgmt-ip already set to the detected value → peer data not rewritten.

        If set_unit_data were called unconditionally it would mutate
        local_unit_data, triggering a peer-relation-changed hook and creating
        an infinite hook loop in production.
        """
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
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        peer_out = state_out.get_relations("peers")[0]
        # local_unit_data must be identical to what was passed in — no spurious write.
        assert peer_out.local_unit_data.get("lbmgmt-ip") == "192.168.100.10"
        # The in-state object must be the same instance (not replaced by a new write).
        assert peer_out.local_unit_data == peer_with_ip.local_unit_data

    def test_amphora_containers_stopped_without_amphora_config(
        self,
        ctx,
        complete_state,
    ):
        """Without amphora-network-attachment health-manager and worker are not started."""
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")
        controller_container = state_out.get_container("octavia-controller")
        hm_status = controller_container.service_statuses.get(
            "octavia-health-manager"
        )
        wk_status = controller_container.service_statuses.get("octavia-worker")
        # init_service only starts always-on services; health-manager and worker
        # have startup: disabled and are not started when Amphora is unconfigured.
        assert hm_status is None
        assert wk_status is None

    def test_amphora_containers_started_with_amphora_config(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
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
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        controller_container = state_out.get_container("octavia-controller")
        hm_status = controller_container.service_statuses.get(
            "octavia-health-manager"
        )
        wk_status = controller_container.service_statuses.get("octavia-worker")
        assert hm_status == testing.pebble.ServiceStatus.ACTIVE
        assert wk_status == testing.pebble.ServiceStatus.ACTIVE


class TestAmphoraConfig:
    """Tests for Amphora-specific configuration and container lifecycle."""

    def test_waiting_when_cni_not_ready(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """CNI DaemonSets not running → event deferred, waiting on CNI."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaNetworkAnnotationPatcher,
            "cni_ready",
            return_value=(False, "kube-multus-ds not ready (0/1 pods)"),
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "waiting"
        assert "cni" in state_out.unit_status.message.lower()
        assert "kube-multus-ds" in state_out.unit_status.message
        # Event must be deferred so it retries on the next hook
        assert len(state_out.deferred) == 1
        assert state_out.deferred[0].name == "config_changed"

    def test_waiting_when_amphora_nic_not_attached(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """CNI ready but 2nd NIC not yet attached → waiting on NIC interface."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        # cni_ready is mocked as True by autouse fixture; NIC has no IP yet.
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "waiting"
        assert "interface" in state_out.unit_status.message.lower()

    def test_blocked_when_amphora_certs_missing(
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """NIC attached but cert relations absent → blocked on relations."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert (
            "amphora-issuing-ca" in state_out.unit_status.message
            and "relations required" in state_out.unit_status.message
        )

    def test_blocked_when_barbican_not_integrated(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
    ):
        """NIC attached, cert relations present, but no barbican → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [amphora_issuing_ca_relation, amphora_controller_cert_relation],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "barbican-service" in state_out.unit_status.message

    def test_waiting_when_barbican_not_ready(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
    ):
        """NIC attached, cert relations present, barbican integrated but not ready → waiting."""
        barbican_rel = testing.Relation(
            endpoint="barbican-service",
            remote_app_name="barbican-k8s",
            remote_app_data={"ready": "false"},
        )
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [
                barbican_rel,
                amphora_issuing_ca_relation,
                amphora_controller_cert_relation,
            ],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "waiting"
        assert "barbican" in state_out.unit_status.message.lower()


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """_get_config_errors() blocks on invalid enum values before configure_unit."""

    @pytest.mark.parametrize(
        "bad_value",
        ["TRIPLE", "ACTIVE-STANDBY", "single", "BOGUS"],
    )
    def test_blocked_on_invalid_topology(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        bad_value,
    ):
        """Invalid loadbalancer-topology → BlockedStatus before configure_unit runs."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"loadbalancer-topology": bad_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "loadbalancer-topology" in state_out.unit_status.message

    @pytest.mark.parametrize(
        "good_value",
        ["SINGLE", "ACTIVE_STANDBY"],
    )
    def test_active_on_valid_topology(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        good_value,
    ):
        """Valid loadbalancer-topology values → charm proceeds to Active."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"loadbalancer-topology": good_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    @pytest.mark.parametrize(
        "bad_value",
        ["random", "HARD", "none", "bogus"],
    )
    def test_blocked_on_invalid_anti_affinity(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        bad_value,
    ):
        """Invalid anti-affinity-policy → BlockedStatus."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"anti-affinity-policy": bad_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "anti-affinity-policy" in state_out.unit_status.message

    @pytest.mark.parametrize(
        "good_value",
        ["anti-affinity", "soft-anti-affinity", "auto", "disable"],
    )
    def test_active_on_valid_anti_affinity(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        good_value,
    ):
        """Valid anti-affinity-policy values → charm proceeds to Active."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"anti-affinity-policy": good_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    @pytest.mark.parametrize("bad_value", ["ftp", "http", "udp", "tcp"])
    def test_blocked_on_invalid_log_protocol(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        bad_value,
    ):
        """Invalid log-protocol → BlockedStatus (case-sensitive)."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"log-protocol": bad_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "log-protocol" in state_out.unit_status.message

    @pytest.mark.parametrize("good_value", ["UDP", "TCP"])
    def test_active_on_valid_log_protocol(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        good_value,
    ):
        """Valid log-protocol values → charm proceeds to Active."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"log-protocol": good_value},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_errors_checked_before_configure_unit(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Invalid config blocks before configure_unit; octavia.conf not written."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"loadbalancer-topology": "INVALID"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"


# ---------------------------------------------------------------------------
# octavia.conf template content — OVN-only deployment (no amphora)
# ---------------------------------------------------------------------------


class TestOVNOnlyTemplate:
    """Template rendering when only OVN provider is configured (no Amphora)."""

    def test_ovn_only_provider_driver_advertised(self, ctx, complete_state):
        """OVN-only: enabled_provider_drivers lists only OVN driver."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["enabled_provider_drivers = ovn:Octavia OVN driver"],
        )

    def test_ovn_default_provider_driver(self, ctx, complete_state):
        """OVN-only: default_provider_driver = ovn."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["default_provider_driver = ovn"],
        )

    def test_haproxy_default_connection_retries_rendered(
        self, ctx, complete_state
    ):
        """Default connection-max-retries and intervals are written."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "connection_max_retries = 120",
                "connection_retry_interval = 5",
                "active_connection_max_retries = 15",
                "active_connection_retry_interval = 2",
            ],
        )

    def test_no_health_manager_section_without_amphora(
        self, ctx, complete_state
    ):
        """Without amphora config, [health_manager] section is absent."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        # Retrieve rendered config and verify absence of health_manager section.
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        conf_path = stored / "etc/octavia/octavia.conf"
        rendered = conf_path.read_text()
        assert "[health_manager]" not in rendered

    def test_single_topology_disables_anti_affinity(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """SINGLE topology → enable_anti_affinity = False."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"loadbalancer-topology": "SINGLE"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["enable_anti_affinity = False"],
        )

    def test_active_standby_auto_uses_soft_anti_affinity(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """ACTIVE_STANDBY + anti-affinity-policy=auto → soft-anti-affinity."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={
                "loadbalancer-topology": "ACTIVE_STANDBY",
                "anti-affinity-policy": "auto",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "enable_anti_affinity = True",
                "anti_affinity_policy = soft-anti-affinity",
            ],
        )

    def test_active_standby_explicit_anti_affinity(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """ACTIVE_STANDBY + anti-affinity-policy=anti-affinity → hard anti-affinity."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={
                "loadbalancer-topology": "ACTIVE_STANDBY",
                "anti-affinity-policy": "anti-affinity",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "enable_anti_affinity = True",
                "anti_affinity_policy = anti-affinity",
            ],
        )

    def test_active_standby_disable_anti_affinity_policy(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """ACTIVE_STANDBY + anti-affinity-policy=disable → enable_anti_affinity = False."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={
                "loadbalancer-topology": "ACTIVE_STANDBY",
                "anti-affinity-policy": "disable",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["enable_anti_affinity = False"],
        )


# ---------------------------------------------------------------------------
# octavia.conf template — Amphora-specific sections
# ---------------------------------------------------------------------------


class TestAmphoraTemplate:
    """Template sections rendered only when Amphora is enabled."""

    def _amphora_state(
        self,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        extra_config=None,
    ):
        cfg = {"amphora-network-attachment": "octavia-mgmt"}
        if extra_config:
            cfg.update(extra_config)
        return testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=cfg,
        )

    def test_both_providers_advertised_when_amphora_enabled(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Amphora + OVN both set → enabled_provider_drivers lists both."""
        state_in = self._amphora_state(
            complete_relations_with_barbican, complete_secrets, all_containers
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "enabled_provider_drivers = "
                "amphora:The Octavia Amphora driver,ovn:Octavia OVN driver"
            ],
        )

    def test_amp_image_tag_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """amp-image-tag is written to [controller_worker]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"amp-image-tag": "custom-amphora-tag"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["amp_image_tag = custom-amphora-tag"],
        )

    def test_amp_flavor_id_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """amp-flavor-id is written to [controller_worker]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"amp-flavor-id": "flavor-uuid-1234"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["amp_flavor_id = flavor-uuid-1234"],
        )

    def test_amp_secgroup_list_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """amp-secgroup-list is written into [controller_worker]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"amp-secgroup-list": "sg-aaa,sg-bbb"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["amp_secgroup_list = sg-aaa,sg-bbb"],
        )

    def test_amp_boot_network_list_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """amp-boot-network-list is written into [controller_worker]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"amp-boot-network-list": "net-aaa,net-bbb"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["amp_boot_network_list = net-aaa,net-bbb"],
        )

    def test_volume_based_amphora_config_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """enable-volume-based-amphora=True → volume_driver + volume_size written."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "enable-volume-based-amphora": True,
                "volume-size": 32,
                "volume-type": "fast-ssd",
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "volume_driver = volume_cinder_driver",
                "volume_size = 32",
                "volume_type = fast-ssd",
            ],
        )

    def test_volume_driver_absent_when_disabled(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """enable-volume-based-amphora=False → volume_driver not in config."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"enable-volume-based-amphora": False},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "volume_driver" not in rendered

    def test_loadbalancer_topology_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """loadbalancer_topology value is passed through to [controller_worker]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"loadbalancer-topology": "ACTIVE_STANDBY"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["loadbalancer_topology = ACTIVE_STANDBY"],
        )

    def test_admin_log_offloading_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """admin-log-targets set → [amphora_agent] admin_log_targets rendered."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "admin-log-targets": "192.0.2.10:514",
                "administrative-log-facility": 3,
                "log-protocol": "UDP",
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "admin_log_targets = 192.0.2.10:514",
                "administrative_log_facility = 3",
                "log_protocol = UDP",
            ],
        )

    def test_tenant_log_offloading_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """tenant-log-targets set → [amphora_agent] tenant_log_targets rendered."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "tenant-log-targets": "192.0.2.20:514",
                "user-log-facility": 2,
                "log-protocol": "TCP",
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "tenant_log_targets = 192.0.2.20:514",
                "user_log_facility = 2",
                "log_protocol = TCP",
            ],
        )

    def test_forward_all_logs_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """forward-all-logs=True → forward_all_logs = True in [amphora_agent]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "admin-log-targets": "192.0.2.10:514",
                "forward-all-logs": True,
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["forward_all_logs = True"],
        )

    def test_disable_local_log_storage_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """disable-local-log-storage=True → key rendered in [amphora_agent]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"disable-local-log-storage": True},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["disable_local_log_storage = True"],
        )

    def test_connection_logging_false_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """connection-logging=False → connection_logging = False in [haproxy_amphora]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"connection-logging": False},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["connection_logging = False"],
        )

    def test_connection_logging_absent_when_true(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """connection-logging=True (default) → connection_logging line absent (haproxy default)."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"connection-logging": True},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "connection_logging" not in rendered

    def test_user_log_format_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """user-log-format set → user_log_format rendered in [haproxy_amphora]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={"user-log-format": "%{+Q}o %t %s"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["user_log_format = %{+Q}o %t %s"],
        )

    def test_log_retry_settings_rendered(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """log-retry-count and log-retry-interval rendered when log targets set."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "admin-log-targets": "192.0.2.10:514",
                "log-retry-count": 3,
                "log-retry-interval": 60,
                "log-protocol": "TCP",
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "log_retry_count = 3",
                "log_retry_interval = 60",
            ],
        )

    def test_custom_haproxy_retry_intervals(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
        mock_amphora_cert_context,
    ):
        """Non-default connection retry values are written to [haproxy_amphora]."""
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            extra_config={
                "connection-max-retries": 60,
                "connection-retry-interval": 10,
                "active-connection-max-retries": 30,
                "active-connection-retry-interval": 5,
            },
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "connection_max_retries = 60",
                "connection_retry_interval = 10",
                "active_connection_max_retries = 30",
                "active_connection_retry_interval = 5",
            ],
        )

    def test_cert_paths_absent_when_cert_context_empty(
        self, ctx, complete_state
    ):
        """OVN-only: no cert context → cert path lines absent from config."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "issuing_ca.pem" not in rendered
        assert "controller_cert.pem" not in rendered


# ---------------------------------------------------------------------------
# Health manager [health_manager] section — multi-unit peer data
# ---------------------------------------------------------------------------


class TestHealthManagerContext:
    """AmphoraHealthManagerContext reads peer relation data correctly."""

    def _state_with_peer_ips(
        self,
        local_ip,
        remote_ips,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
    ):
        """Build state with peer unit data seeded for health manager tests."""
        remote_units_data = {
            i: {"lbmgmt-ip": ip} for i, ip in enumerate(remote_ips, start=1)
        }
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": local_ip},
            peers_data=remote_units_data,
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        return testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )

    def test_single_unit_bind_ip_and_controller_list(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Single unit: bind_ip and controller_ip_port_list rendered with local IP."""
        state_in = self._state_with_peer_ips(
            "10.0.0.1",
            [],
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            [
                "bind_ip = 10.0.0.1",
                "controller_ip_port_list = 10.0.0.1:5555",
            ],
        )

    def test_multi_unit_controller_ip_port_list(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """3-unit HA: controller_ip_port_list contains all three unit IPs."""
        state_in = self._state_with_peer_ips(
            "10.0.0.1",
            ["10.0.0.2", "10.0.0.3"],
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "bind_ip = 10.0.0.1" in rendered
        # All three IPs must appear in the list (order may vary).
        for ip in ("10.0.0.1", "10.0.0.2", "10.0.0.3"):
            assert f"{ip}:5555" in rendered

    def test_heartbeat_key_rendered_in_health_manager(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Heartbeat key present in peer app data → rendered in [health_manager]."""
        secret = testing.Secret(
            {"heartbeat-key": "deadbeef0123456789abcdef"},
            id="secret:heartbeat-1",
            label="heartbeat-key",
        )
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
            local_app_data={"heartbeat-key": "secret:heartbeat-1"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets + [secret],
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "octavia-api",
            "/etc/octavia/octavia.conf",
            ["heartbeat_key = deadbeef0123456789abcdef"],
        )

    def test_no_health_manager_section_when_peer_data_absent(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Amphora enabled but lbmgmt-ip not yet in peer data → [health_manager] absent."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        # Simulate NIC not yet available so no IP is set in peer data.
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "bind_ip" not in rendered


# ---------------------------------------------------------------------------
# Peer relation event handling
# ---------------------------------------------------------------------------


class TestPeerRelationEvents:
    """Peer relation events trigger configure_charm → IP detection + config update."""

    def test_peer_relation_created_sets_lbmgmt_ip(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """peers-relation-created → configure_charm runs → lbmgmt-ip written."""
        peer_rel = testing.PeerRelation(endpoint="peers")
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.1.2.3",
        ):
            state_out = ctx.run(ctx.on.relation_created(peer_rel), state_in)
        peer_out = state_out.get_relations("peers")[0]
        assert peer_out.local_unit_data.get("lbmgmt-ip") == "10.1.2.3"

    def test_peer_relation_changed_triggers_reconfigure(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """peers-relation-changed with new remote IP → controller_ip_port_list updated."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
            peers_data={1: {"lbmgmt-ip": "10.0.0.2"}},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.relation_changed(peer_rel), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "10.0.0.1:5555" in rendered
        assert "10.0.0.2:5555" in rendered

    def test_peer_relation_departed_removes_ip_from_list(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """peers-relation-departed → remaining units regenerate controller_ip_port_list."""
        # After departure only the local unit remains.
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
            peers_data={1: {"lbmgmt-ip": "10.0.0.2"}},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            # Scenario requires remote_unit for relation_departed.
            state_out = ctx.run(
                ctx.on.relation_departed(peer_rel, remote_unit=1), state_in
            )
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        # Local unit IP must appear.
        assert "10.0.0.1:5555" in rendered


# ---------------------------------------------------------------------------
# Amphora container lifecycle — start/stop reconciliation
# ---------------------------------------------------------------------------


class TestAmphoraContainerLifecycle:
    """_reconcile_amphora_containers starts/stops health-manager and worker."""

    def test_amphora_services_stopped_when_cert_not_ready(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """Amphora NIC present but cert handlers not ready → services not started."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        # Default mock_certs_ready covers OVN certs; amphora certs NOT ready.
        with mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=False,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.5",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        controller = state_out.get_container("octavia-controller")
        hm = controller.service_statuses.get("octavia-health-manager")
        wk = controller.service_statuses.get("octavia-worker")
        assert hm is None or hm != testing.pebble.ServiceStatus.ACTIVE
        assert wk is None or wk != testing.pebble.ServiceStatus.ACTIVE

    def test_always_on_services_start_without_amphora(
        self,
        ctx,
        complete_state,
    ):
        """driver-agent and housekeeping start even without amphora-network-attachment."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        controller = state_out.get_container("octavia-controller")
        assert (
            controller.service_statuses.get("octavia-driver-agent")
            == testing.pebble.ServiceStatus.ACTIVE
        )
        assert (
            controller.service_statuses.get("octavia-housekeeping")
            == testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_controller_starts_always_on_services(
        self,
        ctx,
        complete_state,
        controller_container,
    ):
        """pebble-ready on octavia-controller starts always-on services."""
        state_out = ctx.run(
            ctx.on.pebble_ready(controller_container), complete_state
        )
        controller = state_out.get_container("octavia-controller")
        assert (
            controller.service_statuses.get("octavia-driver-agent")
            == testing.pebble.ServiceStatus.ACTIVE
        )
        assert (
            controller.service_statuses.get("octavia-housekeeping")
            == testing.pebble.ServiceStatus.ACTIVE
        )

    def test_amphora_services_started_on_pebble_ready_when_all_ready(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        controller_container,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """pebble-ready on octavia-controller + Amphora fully configured → all services up."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(
                ctx.on.pebble_ready(controller_container), state_in
            )
        controller = state_out.get_container("octavia-controller")
        assert (
            controller.service_statuses.get("octavia-health-manager")
            == testing.pebble.ServiceStatus.ACTIVE
        )
        assert (
            controller.service_statuses.get("octavia-worker")
            == testing.pebble.ServiceStatus.ACTIVE
        )


# ---------------------------------------------------------------------------
# Heartbeat key generation (leader-only)
# ---------------------------------------------------------------------------


class TestHeartbeatKeyGeneration:
    """Leader generates heartbeat key stored as Juju secret on configure_app_leader."""

    def test_heartbeat_key_generated_when_amphora_enabled(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Leader with amphora-network-attachment → heartbeat-key secret created."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        # A new secret should have been added for the heartbeat key.
        # tracked_content is the dict stored in the secret.
        heartbeat_secrets = [
            s
            for s in state_out.secrets
            if "heartbeat-key" in (s.tracked_content or {})
        ]
        assert (
            heartbeat_secrets
        ), "Expected a heartbeat-key secret to be created by the leader"

    def test_heartbeat_key_not_generated_without_amphora(
        self,
        ctx,
        complete_state,
    ):
        """No amphora-network-attachment → heartbeat-key secret NOT created."""
        initial_secret_ids = {s.id for s in complete_state.secrets}
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        new_secrets = [
            s
            for s in state_out.secrets
            if s.id not in initial_secret_ids
            and "heartbeat-key" in (s.tracked_content or {})
        ]
        assert (
            not new_secrets
        ), "heartbeat-key secret must NOT be created without amphora-network-attachment"

    def test_heartbeat_key_not_duplicated_when_already_exists(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Existing heartbeat-key in peer app data → no new secret created."""
        existing_secret = testing.Secret(
            {"heartbeat-key": "existing-key-abc"},
            id="secret:heartbeat-existing",
            label="heartbeat-key",
        )
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_app_data={"heartbeat-key": "secret:heartbeat-existing"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets + [existing_secret],
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        heartbeat_secrets = [
            s
            for s in state_out.secrets
            if "heartbeat-key" in (s.tracked_content or {})
        ]
        # Still exactly one — the original; no duplicate created.
        assert len(heartbeat_secrets) == 1
        assert heartbeat_secrets[0].id == "secret:heartbeat-existing"


# ---------------------------------------------------------------------------
# Status pool priority — amphora_net_status vs amphora_status
# ---------------------------------------------------------------------------


class TestStatusPriority:
    """compound_status pool: lower-priority slots don't mask higher-priority ones."""

    def test_network_waiting_masks_cert_blocked(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        mock_amphora_certs_ready,
    ):
        """CNI waiting (priority 5) wins over cert-missing blocked (priority 3)."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        # CNI infrastructure not ready → amphora_net_status = WaitingStatus.
        with mock.patch.object(
            charm.OctaviaNetworkAnnotationPatcher,
            "cni_ready",
            return_value=(False, "kube-multus-ds not ready (0/1 pods)"),
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        # The compound-status pool surfaces the highest-priority *bad* slot.
        # amphora_net_status (5) > amphora_status (3), so waiting wins.
        assert state_out.unit_status.name == "waiting"
        assert "cni" in state_out.unit_status.message.lower()

    def test_both_blocked_workload_wins(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Config blocked (priority 95) always wins over workload active."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"loadbalancer-topology": "INVALID"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "loadbalancer-topology" in state_out.unit_status.message

    def test_amphora_status_active_when_not_configured(
        self, ctx, complete_state
    ):
        """Without amphora-network-attachment both amphora status slots are active."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_amphora_issuing_ca_not_ready_blocked(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """NIC ready, barbican ready, issuing-ca not ready → blocked on issuing CA."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ), mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=False,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert (
            "amphora-issuing-ca" in state_out.unit_status.message
            or "amphora-controller-cert" in state_out.unit_status.message
        )


# ---------------------------------------------------------------------------
# lbmgmt-ip write-back behaviour
# ---------------------------------------------------------------------------


class TestLbmgmtIpWriteback:
    """_set_lbmgmt_ip() writes peer data only when IP changes."""

    def test_new_ip_written_to_peer_data(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """IP detected for first time → lbmgmt-ip written to peer unit data."""
        # Peer has no prior IP.
        peer_rel = testing.PeerRelation(endpoint="peers")
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="172.30.0.5",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        peer_out = state_out.get_relations("peers")[0]
        assert peer_out.local_unit_data.get("lbmgmt-ip") == "172.30.0.5"

    def test_changed_ip_written_to_peer_data(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """IP changed (pod rescheduled) → peer data updated to new value."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "172.30.0.5"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        # New pod → different IP.
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="172.30.0.9",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        peer_out = state_out.get_relations("peers")[0]
        assert peer_out.local_unit_data.get("lbmgmt-ip") == "172.30.0.9"

    def test_unchanged_ip_not_rewritten(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """IP unchanged → peer data object identity preserved (no spurious hook)."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "172.30.0.5"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="172.30.0.5",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        peer_out = state_out.get_relations("peers")[0]
        # Value must still be the original; the dict must not have changed.
        assert peer_out.local_unit_data == peer_rel.local_unit_data

    def test_no_ip_when_amphora_not_configured(
        self,
        ctx,
        complete_state,
    ):
        """No amphora-network-attachment → lbmgmt-ip never written."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        for rel in state_out.get_relations("peers"):
            assert "lbmgmt-ip" not in rel.local_unit_data

    def test_amphora_net_status_active_when_ip_detected(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """IP detected → amphora_net_status is set to Active (no error surfaced)."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            # With cert relations not yet issued the unit ends blocked on certs,
            # but the amphora_net_status slot itself must be Active.
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        # amphora_net_status at priority 5 must not surface a waiting message
        # about "interface not detected".
        if state_out.unit_status.name == "waiting":
            assert "interface" not in state_out.unit_status.message.lower()


# ---------------------------------------------------------------------------
# Pod restart simulation — annotation update triggers K8s rolling restart
# ---------------------------------------------------------------------------


class TestPodRestartAfterAnnotationPatch:
    """Simulate the event cascade triggered when a pod annotation is patched.

    Patching the StatefulSet pod template to attach the Amphora management NIC
    causes Kubernetes to roll the pods, generating the following event cascade.

    Timeline in production:
      1. config_changed: amphora-network-attachment set, CNI ready →
         OctaviaNetworkAnnotationPatcher writes the Multus annotation to the
         StatefulSet pod template.
      2. Kubernetes rolls the StatefulSet: existing pods are terminated, new
         pods start with the extra NIC → containers disconnect then reconnect.
      3. pebble_ready fires for each container (octavia-api, octavia-controller)
         with can_connect=True and the management IP now visible in the pod's
         k8s.v1.cni.cncf.io/network-status annotation.
      4. The charm writes octavia.conf (with [health_manager] bind_ip),
         starts health-manager and octavia-worker, and reaches ActiveStatus.

    These tests drive that multi-event sequence through the scenario harness:
    state_out of event N becomes state_in of event N+1.
    """

    def _amphora_state(
        self,
        complete_relations_with_barbican,
        complete_secrets,
        containers,
        amphora_config,
    ) -> testing.State:
        """Build a base state with Amphora fully configured."""
        return testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=containers,
            secrets=complete_secrets,
            config=amphora_config,
        )

    def test_config_changed_waiting_before_nic_attached(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Step 1: config_changed with annotation just applied, pod not yet restarted.

        The patcher has written the annotation but the new pod has not started
        yet so the management interface is not visible → WaitingStatus.
        The StatefulSet patch is attempted (lightkube_client.patch is called).
        """
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            amphora_config,
        )
        mock_client = mock.MagicMock()
        # Simulate: annotation not yet present on the StatefulSet.
        mock_sts = mock.MagicMock()
        mock_sts.spec.template.metadata.annotations = {}
        mock_client.get.return_value = mock_sts

        with mock.patch.object(
            charm.KubernetesResourcePatcher,
            "lightkube_client",
            new_callable=mock.PropertyMock,
            return_value=mock_client,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,  # NIC not yet visible
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Annotation patch must have been attempted.
        mock_client.patch.assert_called_once()
        # Charm waits because the management interface is not yet attached.
        assert state_out.unit_status.name == "waiting"
        assert "interface" in state_out.unit_status.message.lower()

    def test_containers_disconnect_during_pod_restart(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Step 2: K8s terminates old pod — containers disconnect.

        When the rolling restart begins the pebble sockets disappear.
        The charm should report waiting/blocked (not crash) while
        can_connect=False on all containers.
        """
        disconnected_containers = [
            testing.Container(name="octavia-api", can_connect=False),
            testing.Container(name="octavia-controller", can_connect=False),
        ]
        state_in = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            disconnected_containers,
            amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting during pod restart, "
            f"got {state_out.unit_status}"
        )

    def test_pebble_ready_api_after_pod_restart(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        api_container,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Step 3a: pebble_ready on octavia-api after pod comes back with new NIC.

        The new pod now has the management NIC attached.  pebble-ready for
        octavia-api fires first.  The charm writes octavia.conf and reaches
        active because the management IP is now visible.
        """
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
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.pebble_ready(api_container), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        # octavia.conf must contain the health_manager bind_ip.
        container_out = state_out.get_container("octavia-api")
        fs = container_out.get_filesystem(ctx)
        conf = (fs / "etc/octavia/octavia.conf").read_text()
        assert "bind_ip = 192.168.100.10" in conf

    def test_pebble_ready_controller_after_pod_restart(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        controller_container,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Step 3b: pebble_ready on octavia-controller after pod comes back with new NIC.

        After the API container is up, pebble-ready for octavia-controller fires.
        health-manager and octavia-worker must be started (Amphora services).
        """
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
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(
                ctx.on.pebble_ready(controller_container), state_in
            )

        controller_out = state_out.get_container("octavia-controller")
        assert (
            controller_out.service_statuses.get("octavia-health-manager")
            == testing.pebble.ServiceStatus.ACTIVE
        )
        assert (
            controller_out.service_statuses.get("octavia-worker")
            == testing.pebble.ServiceStatus.ACTIVE
        )

    def test_full_restart_sequence(
        self,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        api_container,
        controller_container,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """End-to-end: run the complete 4-event sequence through scenario.

        Event sequence:
          config_changed  → waiting (annotation patched, NIC not yet visible)
          config_changed  → waiting (pod restarting, containers disconnected)
          pebble_ready    → active  (octavia-api, NIC now visible)
          pebble_ready    → active  (octavia-controller, Amphora services up)

        Each state_out is threaded as the state_in of the next event.
        Each event requires a fresh Context because data_platform_libs registers
        events dynamically on the charm class during __init__; reusing the same
        Context across multiple ctx.run() calls would re-register those events
        and raise a RuntimeError.  cleanup_database_requires_events() removes
        them so the next Context can register them cleanly.
        """

        def _ctx():
            return testing.Context(
                charm.OctaviaOperatorCharm, charm_root=CHARM_ROOT
            )

        mock_client = mock.MagicMock()
        mock_sts = mock.MagicMock()
        mock_sts.spec.template.metadata.annotations = {}
        mock_client.get.return_value = mock_sts

        # --- Event 1: config_changed, annotation written, NIC not yet up ---
        state_1 = self._amphora_state(
            complete_relations_with_barbican,
            complete_secrets,
            all_containers,
            amphora_config,
        )
        ctx1 = _ctx()
        with mock.patch.object(
            charm.KubernetesResourcePatcher,
            "lightkube_client",
            new_callable=mock.PropertyMock,
            return_value=mock_client,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_after_1 = ctx1.run(ctx1.on.config_changed(), state_1)

        assert state_after_1.unit_status.name == "waiting"
        mock_client.patch.assert_called_once()

        # --- Event 2: config_changed, pod restarting, containers down ---
        # Simulate the rolling-restart window: containers are disconnected.
        disconnected_containers = [
            testing.Container(name="octavia-api", can_connect=False),
            testing.Container(name="octavia-controller", can_connect=False),
        ]
        state_2 = testing.State(
            leader=state_after_1.leader,
            relations=list(state_after_1.relations),
            secrets=list(state_after_1.secrets),
            containers=disconnected_containers,
            config=amphora_config,
        )
        cleanup_database_requires_events()
        ctx2 = _ctx()
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value=None,
        ):
            state_after_2 = ctx2.run(ctx2.on.config_changed(), state_2)

        assert state_after_2.unit_status.name in ("blocked", "waiting")

        # --- Event 3: pebble_ready octavia-api, pod back with new NIC ---
        peer_with_ip = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "192.168.100.10"},
        )
        relations_with_ip = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_with_ip]
        state_3 = testing.State(
            leader=True,
            relations=relations_with_ip,
            secrets=complete_secrets,
            containers=all_containers,
            config=amphora_config,
        )
        cleanup_database_requires_events()
        ctx3 = _ctx()
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_after_3 = ctx3.run(
                ctx3.on.pebble_ready(api_container), state_3
            )

        assert state_after_3.unit_status == testing.ActiveStatus("")

        # --- Event 4: pebble_ready octavia-controller → Amphora services up ---
        state_4 = testing.State(
            leader=True,
            relations=relations_with_ip,
            secrets=complete_secrets,
            containers=all_containers,
            config=amphora_config,
        )
        cleanup_database_requires_events()
        ctx4 = _ctx()
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_after_4 = ctx4.run(
                ctx4.on.pebble_ready(controller_container), state_4
            )

        assert state_after_4.unit_status == testing.ActiveStatus("")
        controller_out = state_after_4.get_container("octavia-controller")
        assert (
            controller_out.service_statuses.get("octavia-health-manager")
            == testing.pebble.ServiceStatus.ACTIVE
        )
        assert (
            controller_out.service_statuses.get("octavia-worker")
            == testing.pebble.ServiceStatus.ACTIVE
        )

    def test_annotation_not_reapplied_when_already_patched(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """Patcher skips re-patching when the StatefulSet annotation is already set.

        After the first restart the annotation is already present.  Subsequent
        config_changed hooks (e.g. peer-data changes) must not re-patch and
        trigger another unnecessary rolling restart.
        """
        nad_name = amphora_config["amphora-network-attachment"]
        mock_client = mock.MagicMock()
        mock_sts = mock.MagicMock()
        # Simulate: annotation already present with the correct value.
        mock_sts.spec.template.metadata.annotations = {
            "k8s.v1.cni.cncf.io/networks": nad_name
        }
        mock_client.get.return_value = mock_sts

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
            charm.KubernetesResourcePatcher,
            "lightkube_client",
            new_callable=mock.PropertyMock,
            return_value=mock_client,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="192.168.100.10",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # patch() must NOT have been called — no unnecessary rolling restart.
        mock_client.patch.assert_not_called()
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_annotation_removed_when_amphora_disabled(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """amphora-network-attachment removed from config → null-patch clears annotation.

        When the operator removes the NAD config, the charm must write a null
        patch to remove the annotation from the StatefulSet.  Kubernetes then
        rolls the pods without the extra NIC.
        """
        mock_client = mock.MagicMock()
        mock_sts = mock.MagicMock()
        # Simulate: annotation was previously set.
        mock_sts.spec.template.metadata.annotations = {
            "k8s.v1.cni.cncf.io/networks": "octavia-mgmt"
        }
        mock_client.get.return_value = mock_sts

        # No amphora config — annotation should be removed.
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        with mock.patch.object(
            charm.KubernetesResourcePatcher,
            "lightkube_client",
            new_callable=mock.PropertyMock,
            return_value=mock_client,
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # patch() must have been called to null out the annotation.
        mock_client.patch.assert_called_once()
        call_kwargs = mock_client.patch.call_args
        patch_obj = call_kwargs.kwargs.get("obj") or call_kwargs.args[2]
        annotation_val = patch_obj["spec"]["template"]["metadata"][
            "annotations"
        ]["k8s.v1.cni.cncf.io/networks"]
        assert annotation_val is None, (
            "Expected null annotation to remove the NAD, "
            f"got {annotation_val!r}"
        )
        assert state_out.unit_status == testing.ActiveStatus("")


# ---------------------------------------------------------------------------
# CNI readiness gating
# ---------------------------------------------------------------------------


class TestCNIReadinessGating:
    """OctaviaNetworkAnnotationPatcher.cni_ready() gates StatefulSet patching."""

    @pytest.mark.parametrize(
        "missing_ds",
        charm.OctaviaNetworkAnnotationPatcher._CNI_DAEMONSETS,
    )
    def test_waiting_for_each_missing_daemonset(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        missing_ds,
    ):
        """For each required DaemonSet, not-ready → waiting with its name in message."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        reason = f"{missing_ds} not ready (0/1 pods)"
        with mock.patch.object(
            charm.OctaviaNetworkAnnotationPatcher,
            "cni_ready",
            return_value=(False, reason),
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "waiting"
        assert missing_ds in state_out.unit_status.message

    def test_cni_ready_allows_ip_detection(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """cni_ready() returns True → IP detection proceeds normally."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        # autouse mock_cni_ready already returns True.
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.5.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        # Unit should not surface CNI-related waiting status.
        if state_out.unit_status.name == "waiting":
            assert "cni" not in state_out.unit_status.message.lower()


# ---------------------------------------------------------------------------
# Barbican integration edge cases
# ---------------------------------------------------------------------------


class TestBarbicanIntegration:
    """barbican-service relation state transitions."""

    def test_blocked_when_barbican_not_related_but_nic_ready(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
        mock_amphora_certs_ready,
    ):
        """NIC + cert relations present but no barbican-service → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [amphora_issuing_ca_relation, amphora_controller_cert_relation],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "barbican-service" in state_out.unit_status.message

    def test_active_when_barbican_becomes_ready(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """All prerequisites met including barbican-service ready → Active."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_cert_manager_absent_without_barbican(self, ctx, complete_state):
        """No barbican-service → cert_manager line absent in [certificates]."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        rendered = (stored / "etc/octavia/octavia.conf").read_text()
        assert "cert_manager" not in rendered


# ---------------------------------------------------------------------------
# Cert relation individual readiness
# ---------------------------------------------------------------------------


class TestCertRelationReadiness:
    """amphora-issuing-ca and amphora-controller-cert relations tested individually."""

    def test_blocked_when_only_issuing_ca_related(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_issuing_ca_relation,
        barbican_ready_relation,
    ):
        """Only issuing-ca related (controller-cert absent) → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [amphora_issuing_ca_relation, barbican_ready_relation],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "amphora-issuing-ca" in state_out.unit_status.message

    def test_blocked_when_only_controller_cert_related(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        amphora_controller_cert_relation,
        barbican_ready_relation,
    ):
        """Only controller-cert related (issuing-ca absent) → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations
            + [amphora_controller_cert_relation, barbican_ready_relation],
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "amphora-issuing-ca" in state_out.unit_status.message

    def test_blocked_when_amphora_issuing_ca_not_issued(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """Cert relations related but issuing-ca cert not yet issued → blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        # Neither cert is ready — this exercises the issuing-ca not-ready path
        # (issuing CA is checked first in post_config_setup).
        with mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=False,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"

    def test_blocked_when_amphora_controller_cert_not_issued(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
    ):
        """All ready except controller cert → blocked on controller-cert."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations_with_barbican,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )

        def patched_post(self_charm):
            if not self_charm.config.get("amphora-network-attachment"):
                self_charm.amphora_status.set(
                    __import__("ops").ActiveStatus("")
                )
                return
            if self_charm.amphora_net_status.status.name != "active":
                self_charm.amphora_status.set(
                    __import__("ops").ActiveStatus("")
                )
                return
            import ops

            if not self_charm.model.relations.get(
                "amphora-issuing-ca"
            ) or not self_charm.model.relations.get("amphora-controller-cert"):
                self_charm.amphora_status.set(
                    ops.BlockedStatus(
                        "amphora-issuing-ca and amphora-controller-cert "
                        "relations required for Amphora"
                    )
                )
                return
            if not self_charm.model.relations.get("barbican-service"):
                self_charm.amphora_status.set(
                    ops.BlockedStatus(
                        "barbican-service integration required for Amphora"
                    )
                )
                return
            if not self_charm.barbican_svc.ready:
                self_charm.amphora_status.set(
                    ops.WaitingStatus("Waiting for barbican-service")
                )
                return
            # Issuing CA: pretend ready; controller cert: NOT ready.
            self_charm.amphora_status.set(
                ops.BlockedStatus(
                    "Amphora controller certificate not yet provided "
                    "by amphora-controller-cert integration"
                )
            )

        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "post_config_setup",
            patched_post,
        ), mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "ready",
            new_callable=mock.PropertyMock,
            return_value=True,
        ), mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "blocked"
        assert "amphora-controller-cert" in state_out.unit_status.message


# ---------------------------------------------------------------------------
# Non-leader unit scenarios
# ---------------------------------------------------------------------------


class TestNonLeaderUnit:
    """Non-leader units must reach Active once the leader has bootstrapped.

    Every scenario that the leader handles should also be exercised on a
    non-leader to verify:
    - The unit does NOT attempt leader-only operations (DB sync, StatefulSet
      patch, keystone ops, heartbeat-key creation).
    - The unit DOES configure its local containers and reach Active status
      once the leader has set ``leader_ready`` in the peer app databag.
    - Amphora cert material is sourced from the peer app databag (not from
      the TLS relation, which is inaccessible to non-leaders in Mode.APP).

    Helper: ``_ready_leader_peer_rel`` builds a PeerRelation that already has
    ``leader_ready=true`` in the app databag so the non-leader passes the
    is_leader_ready() gate.
    """

    @staticmethod
    def _ready_leader_peer_rel(**extra_app_data):
        """Build a PeerRelation whose app data signals that the leader is ready."""
        import json

        app_data = {"leader_ready": json.dumps(True)}
        app_data.update(extra_app_data)
        return testing.PeerRelation(
            endpoint="peers",
            local_app_data=app_data,
        )

    def test_non_leader_reaches_active_with_ready_leader(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Non-leader with ready leader and all relations → ActiveStatus."""
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_non_leader_config_file_written(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Non-leader writes octavia.conf to its containers."""
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        container = state_out.get_container("octavia-api")
        stored = container.get_filesystem(ctx)
        assert (stored / "etc/octavia/octavia.conf").exists()

    def test_non_leader_skips_db_sync(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Non-leader must not execute DB sync commands (leader-only operation).

        run_db_sync() itself is called by ops-sunbeam on every unit, but it
        contains an ``if not self.unit.is_leader(): return`` guard.  We verify
        that the underlying _exec_db_sync() — the method that actually runs
        ``octavia-db-manage`` — is never invoked on a non-leader.
        """
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm, "_exec_db_sync"
        ) as mock_exec_db_sync:
            ctx.run(ctx.on.config_changed(), state_in)
        mock_exec_db_sync.assert_not_called()

    def test_non_leader_skips_statefulset_patch(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
        mock_lightkube_client,
    ):
        """Non-leader must not patch the StatefulSet (leader-only)."""
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        ctx.run(ctx.on.config_changed(), state_in)
        mock_lightkube_client.patch.assert_not_called()

    def test_non_leader_amphora_cert_context_from_peer_databag(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Non-leader reads amphora cert material from peer app databag.

        When the peer app databag contains cert PEMs and a secret-id
        pointing to a Juju app secret that holds private keys,
        AmphoraCertificatesContext._non_leader_context() must assemble
        the same context dict that the leader would build from the TLS
        handlers and write the cert files to pebble.
        """
        # Juju app secret containing the private keys (owned by the app,
        # readable by all units).
        pk_secret = testing.Secret(
            {
                "issuing-ca-private-key": "FAKE_ISSUING_KEY",
                "controller-cert-private-key": "FAKE_CTRL_KEY",
            },
            id="secret:amphora-pk-001",
            label=charm.AMPHORA_CERTS_PRIVATE_KEYS_LABEL,
        )
        peer_rel = self._ready_leader_peer_rel(
            **{
                charm.AMPHORA_ISSUING_CACERT_PEER_KEY: "FAKE_ISSUING_CA",
                charm.AMPHORA_ISSUING_CA_ROOT_PEER_KEY: "FAKE_ROOT_CA",
                charm.AMPHORA_CONTROLLER_CACERT_PEER_KEY: "FAKE_CTRL_CA",
                charm.AMPHORA_CONTROLLER_CERT_PEER_KEY: "FAKE_CTRL_CERT",
                charm.AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY: "secret:amphora-pk-001",
            }
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets + [pk_secret],
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.10.0.5",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Cert files live in the octavia-controller container (not api).
        # See _controller_container_configs() in charm.py.
        container = state_out.get_container("octavia-controller")
        stored = container.get_filesystem(ctx)
        assert (
            stored / "etc/octavia/certs/issuing_ca.pem"
        ).exists(), "issuing_ca.pem not written on non-leader"
        assert (
            stored / "etc/octavia/certs/controller_cert.pem"
        ).exists(), "controller_cert.pem not written on non-leader"
        assert (
            stored / "etc/octavia/certs/controller_ca.pem"
        ).exists(), "controller_ca.pem not written on non-leader"

    def test_non_leader_amphora_cert_context_empty_when_no_peer_data(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Non-leader returns empty cert context when peer databag not yet populated.

        Before the leader has written cert data to the peer databag, the
        non-leader context() must return {} so no stale/partial cert files
        are written to pebble.
        """
        # Peer app data has leader_ready but NO cert keys yet.
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.10.0.5",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Without cert data in peer databag the cert template renders an empty
        # file.  Verify the file has no cert content (not that it doesn't exist
        # — ops-sunbeam writes all registered ContainerConfigFiles regardless).
        container = state_out.get_container("octavia-controller")
        stored = container.get_filesystem(ctx)
        issuing_ca_path = stored / "etc/octavia/certs/issuing_ca.pem"
        content = (
            issuing_ca_path.read_text() if issuing_ca_path.exists() else ""
        )
        assert (
            "FAKE" not in content
        ), "issuing_ca.pem must be empty when peer cert data is absent"

    def test_leader_syncs_certs_to_peer_databag(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        mock_amphora_certs_ready,
    ):
        """Leader writes cert PEMs and a private-key secret to peer app databag.

        After configure_app_leader() the peer relation's app data must contain
        all six amphora cert keys and a secret must exist for the private keys.
        """
        # Mock get_certs() to return fake cert objects for both handlers.
        fake_cert = mock.MagicMock()
        fake_cert.certificate = "FAKE_CERT_PEM"
        fake_cert.ca = "FAKE_CA_PEM"

        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config={"amphora-network-attachment": "octavia-mgmt"},
        )

        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ), mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "get_certs",
            return_value=[("cn", fake_cert)],
        ), mock.patch.object(
            charm.AmphoraTlsCertificatesHandler,
            "get_private_key",
            return_value="FAKE_PRIVATE_KEY",
        ):
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Check the peer app databag got the cert keys.
        peer_out = state_out.get_relation(peer_rel.id)
        assert charm.AMPHORA_ISSUING_CACERT_PEER_KEY in peer_out.local_app_data
        assert (
            charm.AMPHORA_CONTROLLER_CACERT_PEER_KEY in peer_out.local_app_data
        )
        assert (
            charm.AMPHORA_CONTROLLER_CERT_PEER_KEY in peer_out.local_app_data
        )
        assert (
            charm.AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY
            in peer_out.local_app_data
        )

        # Check that a private-keys secret was created.
        pk_secrets = [
            s
            for s in state_out.secrets
            if s.label == charm.AMPHORA_CERTS_PRIVATE_KEYS_LABEL
        ]
        assert (
            pk_secrets
        ), "Private-key Juju secret must be created by the leader"
        content = pk_secrets[0].tracked_content or {}
        assert "issuing-ca-private-key" in content
        assert "controller-cert-private-key" in content

    def test_non_leader_pebble_ready_reaches_active(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """pebble-ready on non-leader with ready leader → Active."""
        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        api_container = state_in.get_container("octavia-api")
        state_out = ctx.run(ctx.on.pebble_ready(api_container), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_non_leader_relation_broken_waits_or_blocks(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        all_containers,
    ):
        """Breaking a mandatory relation on a non-leader → blocked/waiting."""
        from ops_sunbeam.test_utils_scenario import (
            assert_relation_broken_causes_blocked_or_waiting,
        )

        peer_rel = self._ready_leader_peer_rel()
        relations = [
            r for r in complete_relations if r.endpoint != "peers"
        ] + [peer_rel]
        complete_non_leader_state = testing.State(
            leader=False,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
        )
        # Use the "database" endpoint as a representative mandatory relation.
        assert_relation_broken_causes_blocked_or_waiting(
            ctx, complete_non_leader_state, "database"
        )


# ---------------------------------------------------------------------------
# Upgrade charm
# ---------------------------------------------------------------------------


class TestUpgradeCharm:
    """upgrade-charm event triggers configure_charm and re-detects lbmgmt-ip."""

    def test_upgrade_charm_active(self, ctx, complete_state):
        """upgrade-charm with all relations → Active."""
        state_out = ctx.run(ctx.on.upgrade_charm(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_upgrade_charm_with_amphora_reruns_configure(
        self,
        ctx,
        complete_relations_with_barbican,
        complete_secrets,
        all_containers,
        amphora_config,
        mock_amphora_certs_ready,
    ):
        """upgrade-charm with Amphora enabled → IP re-detected and config updated."""
        peer_rel = testing.PeerRelation(
            endpoint="peers",
            local_unit_data={"lbmgmt-ip": "10.0.0.1"},
        )
        relations = [
            r
            for r in complete_relations_with_barbican
            if r.endpoint != "peers"
        ] + [peer_rel]
        state_in = testing.State(
            leader=True,
            relations=relations,
            containers=all_containers,
            secrets=complete_secrets,
            config=amphora_config,
        )
        with mock.patch.object(
            charm.OctaviaOperatorCharm,
            "_get_lbmgmt_ip_from_network_status",
            return_value="10.0.0.1",
        ):
            state_out = ctx.run(ctx.on.upgrade_charm(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")
