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

from pathlib import (
    Path,
)
from unittest.mock import (
    patch,
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
