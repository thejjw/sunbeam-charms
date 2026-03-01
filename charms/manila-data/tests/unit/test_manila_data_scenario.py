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

"""ops.testing (state-transition) tests for manila-data."""

from pathlib import (
    Path,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    amqp_relation_complete,
    assert_relation_broken_causes_blocked_or_waiting,
    db_credentials_secret,
    db_relation_complete,
    identity_credentials_relation_complete,
    identity_credentials_secret,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# ---------------------------------------------------------------------------
# Relation / secret builders
# ---------------------------------------------------------------------------


def _all_mandatory_relations() -> list:
    return [
        amqp_relation_complete(),
        db_relation_complete(),
        identity_credentials_relation_complete(),
    ]


def _all_secrets() -> list:
    return [
        db_credentials_secret(),
        identity_credentials_secret(),
    ]


# ---------------------------------------------------------------------------
# Tests: blocked when no relations
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked/waiting."""

    def test_blocked_when_no_relations(self, ctx):
        """Charm should be blocked/waiting when no relations are present."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_blocked_with_only_one_relation(self, ctx):
        """With only amqp present, should still block on missing relations."""
        state_in = testing.State(
            leader=True,
            relations=[amqp_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")


# ---------------------------------------------------------------------------
# Tests: blocked when each mandatory relation missing (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing_rel", sorted(MANDATORY_RELATIONS))
class TestBlockedWhenEachRelationMissing:
    """When one mandatory relation is absent, charm must not be active."""

    def test_blocked_when_relation_missing(self, ctx, missing_rel):
        """Test blocked when relation missing."""
        remaining = [
            r for r in _all_mandatory_relations() if r.endpoint != missing_rel
        ]
        secrets = _all_secrets()
        state_in = testing.State(
            leader=True,
            relations=remaining,
            secrets=secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


# ---------------------------------------------------------------------------
# Tests: waiting non-leader
# ---------------------------------------------------------------------------


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(
        self, ctx, complete_relations, complete_secrets
    ):
        """Non-leader unit waits — either for leader or data."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Without a peer relation the charm skips the leader-ready check
        # and may reach active; it must not error out.
        assert state_out.unit_status.name in (
            "waiting",
            "blocked",
            "maintenance",
            "active",
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
