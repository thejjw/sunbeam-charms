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

"""Scenario (ops.testing state-transition) tests for ceilometer-k8s."""

from pathlib import (
    Path,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_exists,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    k8s_container,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)


def _ceilometer_container(name, can_connect=True):
    """Create a ceilometer container with exec mocks."""
    execs = [
        testing.Exec(command_prefix=["ceilometer-upgrade"], return_code=0),
    ]
    return k8s_container(name, can_connect=can_connect, execs=execs)


class TestAllRelations:
    """With all relations complete the charm reaches active."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations -> ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_file_written(self, ctx, complete_state):
        """All relations present -> ceilometer.conf is rendered in both containers."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        for container_name in (
            "ceilometer-central",
            "ceilometer-notification",
        ):
            assert_config_file_exists(
                state_out,
                ctx,
                container_name,
                "/etc/ceilometer/ceilometer.conf",
            )


class TestPebbleReady:
    """Pebble-ready event with all relations -> container configured."""

    def test_pebble_ready_central(self, ctx, complete_state):
        """Pebble-ready on ceilometer-central configures the service."""
        container = complete_state.get_container("ceilometer-central")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("ceilometer-central")
        assert "ceilometer-central" in out_container.layers
        layer = out_container.layers["ceilometer-central"]
        assert "ceilometer-central" in layer.to_dict().get("services", {})

    def test_pebble_ready_notification(self, ctx, complete_state):
        """Pebble-ready on ceilometer-notification configures the service."""
        container = complete_state.get_container("ceilometer-notification")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("ceilometer-notification")
        assert "ceilometer-notification" in out_container.layers
        layer = out_container.layers["ceilometer-notification"]
        assert "ceilometer-notification" in layer.to_dict().get("services", {})

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations -> blocked."""
        containers = [
            _ceilometer_container("ceilometer-central"),
            _ceilometer_container("ceilometer-notification"),
        ]
        state_in = testing.State(leader=True, containers=containers)
        container = state_in.get_container("ceilometer-central")
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations -> blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all -> blocked with 'integration missing'."""
        containers = [
            _ceilometer_container("ceilometer-central", can_connect=False),
            _ceilometer_container(
                "ceilometer-notification", can_connect=False
            ),
        ]
        state_in = testing.State(leader=True, containers=containers)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Removing each mandatory relation one at a time -> blocked/waiting."""

    @pytest.mark.parametrize(
        "missing_rel",
        sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_relation_missing(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        containers,
        missing_rel,
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=containers,
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
        self, ctx, complete_relations, complete_secrets, containers
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=containers,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("waiting", "maintenance")
        assert (
            "not bootstrapped" in state_out.unit_status.message.lower()
            or "not ready" in state_out.unit_status.message.lower()
        )


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
