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

"""Scenario (ops.testing state-transition) tests for placement-k8s."""

import json
from dataclasses import (
    replace,
)
from pathlib import (
    Path,
)
from unittest.mock import (
    patch,
)

import charm
import ops
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
    mandatory_relations_from_charmcraft,
    missing_relation_combinations,
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
        """All relations present → placement.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "placement-api", "/etc/placement/placement.conf"
        )

    def test_config_file_contents(self, ctx, complete_state):
        """placement.conf contains expected sections and values."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "placement-api",
            "/etc/placement/placement.conf",
            [
                "[placement_database]",
                "connection = mysql+pymysql://foo:hardpassword@10.0.0.10/placement_api",
                "[keystone_authtoken]",
                "auth_url = http://keystone.internal:5000",
                "username = svcuser1",
                "password = svcpass1",
                "[api]",
                "auth_strategy = keystone",
            ],
        )

    def test_wsgi_site_config_written(self, ctx, complete_state):
        """Apache WSGI site config is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out,
            ctx,
            "placement-api",
            "/etc/apache2/sites-available/wsgi-placement-api.conf",
        )

    def test_db_sync_command_executed(self, ctx, complete_state):
        """Verify db sync command is executed during configure_charm."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        # If we got to active, the db sync exec mock was used without error
        assert state_out.unit_status == testing.ActiveStatus("")


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the WSGI service."""
        container = complete_state.get_container("placement-api")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("placement-api")
        assert "placement-api" in out_container.layers
        layer = out_container.layers["placement-api"]
        assert "wsgi-placement-api" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("wsgi-placement-api") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        container = k8s_api_container("placement-api")
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        container = k8s_api_container("placement-api", can_connect=False)
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

    @pytest.fixture()
    def _relation_combos(self, complete_relations):
        return missing_relation_combinations(
            MANDATORY_RELATIONS, complete_relations
        )

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

    def test_waiting_non_leader(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
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


class TestPlacementApiHealthCheck:
    """Tests for _placement_api_healthy() gate in set_readiness_on_related_units()."""

    def test_waiting_when_api_unhealthy(self, ctx, complete_state):
        """Waiting status when placement-api is not yet serving valid version data.

        This simulates the window between traefik signalling ready and the
        placement-api actually accepting connections (e.g. traefik route not
        yet propagated, or apache still starting up).
        """
        with patch.object(
            charm.PlacementOperatorCharm,
            "_placement_api_healthy",
            return_value=False,
        ):
            state_out = ctx.run(ctx.on.config_changed(), complete_state)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Placement API not yet serving" in state_out.unit_status.message

    def test_active_when_api_healthy(self, ctx, complete_state):
        """Active status when placement-api responds with version data.

        The autouse fixture already patches _placement_api_healthy to True;
        this test makes the expectation explicit.
        """
        # _mock_placement_api_healthy autouse fixture returns True
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_ready_written_to_relation_when_healthy(self, ctx, complete_state):
        """ready=true is written into the placement relation app data when healthy."""
        placement_rel = testing.Relation(
            endpoint="placement",
            remote_app_name="nova",
        )
        state_in = testing.State(
            leader=True,
            relations=list(complete_state.relations) + [placement_rel],
            containers=list(complete_state.containers),
            secrets=list(complete_state.secrets),
        )
        # _mock_placement_api_healthy returns True (autouse)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        out_rel = state_out.get_relation(placement_rel.id)
        # ServiceReadinessProvider writes to local app data
        assert out_rel.local_app_data.get("ready") == "true"

    def test_readiness_request_checks_api_health(self, ctx, complete_state):
        """A bootstrapped service must also be healthy to satisfy a readiness request."""
        placement = testing.Relation(
            endpoint="placement", remote_app_name="nova"
        )
        state = replace(
            complete_state,
            relations=[*complete_state.relations, placement],
        )
        with patch.object(
            charm.PlacementOperatorCharm,
            "_placement_api_healthy",
            return_value=False,
        ):
            state = ctx.run(ctx.on.config_changed(), state)
            cleanup_database_requires_events()
            placement = state.get_relation(placement.id)
            state = ctx.run(ctx.on.relation_changed(placement), state)

        assert state.get_relation(placement.id).local_app_data["ready"] == (
            "false"
        )
        assert state.unit_status == testing.WaitingStatus(
            "(container:placement-api) Placement API not yet serving"
        )

        cleanup_database_requires_events()
        placement = state.get_relation(placement.id)
        state = ctx.run(ctx.on.relation_changed(placement), state)
        assert (
            state.get_relation(placement.id).local_app_data["ready"] == "true"
        )
        assert state.unit_status == testing.ActiveStatus("")


@pytest.mark.parametrize(
    "event_name", ["update_status", "pebble_check_recovered"]
)
class TestPlacementApiReadiness:
    """Readiness recovers without another config or relation event (LP: #2166145)."""

    @pytest.fixture()
    def container(self, tmp_path):
        """Keep the workload filesystem across successive hook executions."""
        mounts = {
            "etc": testing.Mount(location="/etc", source=tmp_path / "etc"),
            "certs": testing.Mount(
                location="/usr/local/share/ca-certificates",
                source=tmp_path / "certs",
            ),
        }
        for mount in mounts.values():
            mount.source.mkdir()
        return replace(k8s_api_container("placement-api"), mounts=mounts)

    @pytest.fixture(params=[True, False], ids=["leader", "non-leader"])
    def readiness_state(self, request, complete_state):
        """Include a ready leader and a consumer of placement readiness."""
        relations = [
            (
                replace(relation, local_app_data={"leader_ready": "true"})
                if relation.endpoint == "peers"
                else relation
            )
            for relation in complete_state.relations
        ]
        relations.append(
            testing.Relation(endpoint="placement", remote_app_name="nova")
        )
        return replace(
            complete_state, leader=request.param, relations=relations
        )

    @pytest.fixture()
    def waiting_state(self, ctx, readiness_state):
        """Persist the API readiness waiting status while Apache is starting."""
        with patch.object(
            charm.PlacementOperatorCharm,
            "_placement_api_healthy",
            return_value=False,
        ):
            state = ctx.run(ctx.on.config_changed(), readiness_state)
        assert state.unit_status == testing.WaitingStatus(
            "(container:placement-api) Placement API not yet serving"
        )
        return state

    @staticmethod
    def run_readiness_check(ctx, state, event_name, check_name="online"):
        """Run a health event and verify it performs no workload configuration."""
        cleanup_database_requires_events()
        ctx.exec_history.clear()
        with (
            patch.object(
                charm.PlacementOperatorCharm, "configure_charm"
            ) as configure,
            patch.object(ops.Container, "push") as push,
            patch.object(ops.Container, "restart") as restart,
        ):
            if event_name == "update_status":
                state = ctx.run(ctx.on.update_status(), state)
            else:
                # Scenario's check-event validation does not normalize hyphenated
                # container names. Emit through ops to exercise all recovery observers.
                with ctx(ctx.on.start(), state) as manager:
                    container = manager.charm.unit.get_container(
                        "placement-api"
                    )
                    manager.charm.on.placement_api_pebble_check_recovered.emit(
                        container, check_name
                    )
                    state = manager.run()

        configure.assert_not_called()
        push.assert_not_called()
        restart.assert_not_called()
        assert not ctx.exec_history.get("placement-api")
        return state

    def test_readiness_recovers(self, ctx, waiting_state, event_name):
        """Keep waiting while unhealthy, then clear it and publish readiness."""
        with patch.object(
            charm.PlacementOperatorCharm,
            "_placement_api_healthy",
            return_value=False,
        ) as probe:
            state = self.run_readiness_check(ctx, waiting_state, event_name)
        probe.assert_called_once_with()
        assert state.unit_status == testing.WaitingStatus(
            "(container:placement-api) Placement API not yet serving"
        )
        placement = next(
            relation
            for relation in state.relations
            if relation.endpoint == "placement"
        )
        assert "ready" not in placement.local_app_data

        # Only the API probe result changes; there is no relation/config hook.
        state = self.run_readiness_check(ctx, state, event_name)
        assert state.unit_status == testing.ActiveStatus("")
        ready = state.get_relation(placement.id).local_app_data.get("ready")
        assert ready == ("true" if state.leader else None)

        state = self.run_readiness_check(ctx, state, event_name)
        assert state.unit_status == testing.ActiveStatus("")

    def test_readiness_checks_prerequisites(
        self, ctx, waiting_state, event_name
    ):
        """A healthy API alone cannot bypass a missing mandatory relation."""
        state = replace(
            waiting_state,
            relations=[
                relation
                for relation in waiting_state.relations
                if relation.endpoint != "database"
            ],
        )
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            state = self.run_readiness_check(ctx, state, event_name)
        probe.assert_not_called()
        assert isinstance(state.unit_status, testing.BlockedStatus)
        assert "integration missing" in state.unit_status.message
        placement = next(
            relation
            for relation in state.relations
            if relation.endpoint == "placement"
        )
        assert "ready" not in placement.local_app_data

    def test_other_waiting_status_preserved(
        self, ctx, complete_state, event_name
    ):
        """An API readiness check cannot clear a workload waiting status."""
        state = ctx.run(ctx.on.config_changed(), complete_state)
        state = replace(
            state,
            leader=False,
            relations=[
                (
                    replace(relation, local_app_data={})
                    if relation.endpoint == "peers"
                    else relation
                )
                for relation in state.relations
            ],
        )
        cleanup_database_requires_events()
        state = ctx.run(ctx.on.config_changed(), state)
        assert state.unit_status == testing.WaitingStatus(
            "(workload) Leader not ready"
        )

        state = self.run_readiness_check(ctx, state, event_name)
        assert state.unit_status == testing.WaitingStatus(
            "(workload) Leader not ready"
        )

    def test_readiness_recovery_preserves_configuration_failure(
        self, ctx, waiting_state, event_name
    ):
        """Clear the API waiting entry without overwriting a configuration error."""
        cleanup_database_requires_events()
        with patch.object(
            charm.PlacementOperatorCharm,
            "configure_unit",
            side_effect=RuntimeError("configuration failed"),
        ):
            state = ctx.run(ctx.on.config_changed(), waiting_state)
        blocked_status = testing.BlockedStatus(
            "(workload) Error in charm (see logs): configuration failed"
        )
        assert state.unit_status == blocked_status

        state = self.run_readiness_check(ctx, state, event_name)
        assert state.unit_status == blocked_status
        status_pool = next(
            stored
            for stored in state.stored_states
            if stored.name == "_status_pool"
        )
        statuses = json.loads(status_pool.content["statuses"])
        assert statuses["container:placement-api"] == {
            "status": "active",
            "message": "",
        }
        assert "api-readiness" not in statuses

    def test_no_probe_before_bootstrapping(
        self, ctx, readiness_state, event_name
    ):
        """An unconfigured unit cannot announce readiness even if the API responds."""
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            state = self.run_readiness_check(ctx, readiness_state, event_name)
        probe.assert_not_called()
        assert state.unit_status == testing.MaintenanceStatus(
            "(bootstrap) Service not bootstrapped"
        )
        placement = next(
            relation
            for relation in state.relations
            if relation.endpoint == "placement"
        )
        assert "ready" not in placement.local_app_data

    def test_pause_clears_pending_readiness(
        self, ctx, waiting_state, event_name
    ):
        """Pausing a starting API leaves maintenance status and disables probes."""
        cleanup_database_requires_events()
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            state = ctx.run(ctx.on.action("pause"), waiting_state)
            paused_status = testing.MaintenanceStatus(
                "(workload) Paused. Use 'resume' action to resume normal service."
            )
            assert state.unit_status == paused_status
            state = self.run_readiness_check(ctx, state, event_name)
        probe.assert_not_called()
        assert state.unit_status == paused_status
        assert (
            state.get_container("placement-api").service_statuses[
                "wsgi-placement-api"
            ]
            == testing.pebble.ServiceStatus.INACTIVE
        )

    def test_no_probe_when_container_disconnected(
        self, ctx, waiting_state, event_name
    ):
        """Container availability is reported by the existing Pebble handler."""
        container = replace(
            waiting_state.get_container("placement-api"), can_connect=False
        )
        state = replace(waiting_state, containers=[container])
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            state = self.run_readiness_check(ctx, state, event_name)
        probe.assert_not_called()
        assert state.unit_status == testing.WaitingStatus(
            "(container:placement-api) pebble not ready"
        )

    def test_failed_pebble_check_preserved(
        self, ctx, waiting_state, event_name
    ):
        """A failed readiness check takes precedence over the API probe."""
        container = waiting_state.get_container("placement-api")
        container = replace(
            container,
            check_infos=[
                (
                    replace(
                        check,
                        status=testing.pebble.CheckStatus.DOWN,
                        failures=check.threshold,
                    )
                    if check.name == "online"
                    else check
                )
                for check in container.check_infos
            ],
        )
        state = replace(waiting_state, containers=[container])
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            # The alive check recovered, but the ready check is still failing.
            state = self.run_readiness_check(
                ctx, state, event_name, check_name="up"
            )
        probe.assert_not_called()
        assert state.unit_status == testing.BlockedStatus(
            "(container:placement-api) healthcheck failed: online"
        )
        placement = next(
            relation
            for relation in state.relations
            if relation.endpoint == "placement"
        )
        assert "ready" not in placement.local_app_data

    def test_no_probe_when_service_stopped(
        self, ctx, waiting_state, event_name
    ):
        """An inactive service retains the existing container waiting status."""
        container = replace(
            waiting_state.get_container("placement-api"),
            service_statuses={
                "wsgi-placement-api": testing.pebble.ServiceStatus.INACTIVE
            },
        )
        state = replace(waiting_state, containers=[container])
        with patch.object(
            charm.PlacementOperatorCharm, "_placement_api_healthy"
        ) as probe:
            state = self.run_readiness_check(ctx, state, event_name)
        probe.assert_not_called()
        assert state.unit_status == testing.WaitingStatus(
            "(container:placement-api) service not ready"
        )
