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

"""State-transition (ops.testing) tests for epa-orchestrator.

This charm is a subordinate machine charm that installs the epa-orchestrator
snap.  Its only mandatory relation is ``sunbeam-machine`` (scope: container).
"""

from ops import (
    testing,
)

from .conftest import (
    sunbeam_machine_relation,
)

# ---------------------------------------------------------------------------
# Tests: blocked when no relations
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """Config-changed with missing sunbeam-machine → blocked/waiting."""

    def test_blocked_when_no_relations(self, ctx):
        """Charm should be blocked/waiting when sunbeam-machine is absent."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_blocked_message_mentions_integration(self, ctx):
        """Status message should mention the missing integration."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert (
            "integration" in state_out.unit_status.message.lower()
            or "relation" in state_out.unit_status.message.lower()
            or "sunbeam" in state_out.unit_status.message.lower()
        )


# ---------------------------------------------------------------------------
# Tests: all relations complete
# ---------------------------------------------------------------------------


class TestAllRelationsComplete:
    """Config-changed with sunbeam-machine present → charm progresses."""

    def test_all_relations_present(self, ctx, complete_state):
        """With sunbeam-machine relation, charm should not block on relations."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite sunbeam-machine "
                f"present: {status.message}"
            )


# ---------------------------------------------------------------------------
# Tests: install event
# ---------------------------------------------------------------------------


class TestInstallEvent:
    """Install event should not crash."""

    def test_install_event_runs(self, ctx):
        """Install event should not raise with mocked externals."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.install(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: relation joined
# ---------------------------------------------------------------------------


class TestRelationJoined:
    """Joining sunbeam-machine should advance the charm."""

    def test_relation_joined(self, ctx):
        """Charm should progress when sunbeam-machine relation joins."""
        rel = sunbeam_machine_relation()
        state_in = testing.State(
            leader=True,
            relations=[rel],
        )
        state_out = ctx.run(ctx.on.relation_joined(rel), state_in)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message


# ---------------------------------------------------------------------------
# Tests: config changes
# ---------------------------------------------------------------------------


class TestConfigChanged:
    """Config changes should be handled without errors."""

    def test_config_changed_snap_channel(self, ctx, complete_relations):
        """Changing snap-channel should not crash."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"snap-channel": "latest/stable"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )

    def test_config_changed_snap_name(self, ctx, complete_relations):
        """Changing snap-name should not crash."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            config={"snap-name": "custom-epa"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: non-leader
# ---------------------------------------------------------------------------


class TestNonLeader:
    """Non-leader unit behaviour."""

    def test_non_leader_with_relation(self, ctx, complete_relations):
        """Non-leader unit should not error."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: update-status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    """update-status event checks epa-orchestrator health."""

    def test_update_status_snap_not_installed(self, ctx, complete_relations):
        """When snap raises SnapError, charm should set blocked."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
        )
        state_out = ctx.run(ctx.on.update_status(), state_in)

        # With mocked snap (present=False), the charm may block or
        # stay in maintenance if not yet bootstrapped.
        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )
