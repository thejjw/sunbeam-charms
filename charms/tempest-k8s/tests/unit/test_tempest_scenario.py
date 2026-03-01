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

"""Scenario (ops.testing state-transition) tests for tempest-k8s."""

import dataclasses
import sys
from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    k8s_container,
    mandatory_relations_from_charmcraft,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# conftest is not importable as a regular module in all pytest setups,
# so we import the local factories via sys.path.
_tests_unit = Path(__file__).parent
if str(_tests_unit) not in sys.path:
    sys.path.insert(0, str(_tests_unit))

from conftest import (  # noqa: E402
    tempest_container,
)


class TestAllRelations:
    """With all relations complete the charm reaches active and configures the service."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the cron service."""
        container = complete_state.get_container("tempest")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("tempest")
        assert "tempest" in out_container.layers
        layer = out_container.layers["tempest"]
        assert "tempest" in layer.to_dict().get("services", {})

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        container = tempest_container()
        state_in = testing.State(
            leader=True,
            containers=[container],
            relations=[peer_relation()],
        )
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        container = k8s_container("tempest", can_connect=False)
        state_in = testing.State(
            leader=True,
            containers=[container],
            relations=[peer_relation()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


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
        # Ensure peer relation is always present
        if not any(r.endpoint == "peers" for r in remaining):
            remaining.append(peer_relation())
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


class TestStart:
    """Start event clears tempest-ready flag."""

    def test_start_clears_tempest_ready(
        self, ctx, complete_state, monkeypatch
    ):
        """Start event resets tempest-ready."""
        calls = []
        monkeypatch.setattr(
            charm.TempestOperatorCharm,
            "set_tempest_ready",
            lambda self, ready: calls.append(ready),
        )
        ctx.run(ctx.on.start(), complete_state)
        assert False in calls


class TestUpgradeCharm:
    """Upgrade-charm event clears tempest-ready flag."""

    def test_upgrade_clears_tempest_ready(
        self, ctx, complete_state, monkeypatch
    ):
        """Upgrade-charm resets tempest-ready."""
        calls = []
        monkeypatch.setattr(
            charm.TempestOperatorCharm,
            "set_tempest_ready",
            lambda self, ready: calls.append(ready),
        )
        ctx.run(ctx.on.upgrade_charm(), complete_state)
        assert False in calls


class TestBlockedInvalidSchedule:
    """Config-changed with an invalid schedule → blocked."""

    def test_blocked_with_invalid_schedule(self, ctx, complete_state):
        """Invalid cron schedule blocks the charm."""
        state_in = dataclasses.replace(
            complete_state, config={"schedule": "* *"}
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "invalid schedule" in state_out.unit_status.message


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
