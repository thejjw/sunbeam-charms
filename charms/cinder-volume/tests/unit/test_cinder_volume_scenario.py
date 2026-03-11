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

"""ops.testing (state-transition) tests for cinder-volume.

This charm is a machine charm (no Pebble containers).  The mandatory
relations are: amqp, database, identity-credentials (requires) and
storage-backend (provides, but marked mandatory by the charm class).
"""

import base64
import json
from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
)

import charm
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
# Relation / secret builders for cinder-volume-specific endpoints
# ---------------------------------------------------------------------------


def storage_backend_relation() -> testing.Relation:
    """storage-backend provides relation with ready flag."""
    return testing.Relation(
        endpoint="storage-backend",
        remote_app_name="cinder",
        remote_app_data={"ready": "true"},
    )


def receive_ca_cert_relation(
    ca: str = "TEST_CA", chain: list[str] | None = None
) -> testing.Relation:
    """receive-ca-cert relation carrying a CA and optional chain."""
    return testing.Relation(
        endpoint="receive-ca-cert",
        remote_app_name="keystone",
        remote_units_data={
            0: {
                "ca": ca,
                "chain": json.dumps(chain or []),
            }
        },
    )


def _all_mandatory_relations() -> list:
    return [
        amqp_relation_complete(),
        db_relation_complete(),
        identity_credentials_relation_complete(),
        storage_backend_relation(),
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


def _relation_builder(name: str):
    """Return the complete relation builder for a mandatory endpoint."""
    builders = {
        "amqp": amqp_relation_complete,
        "database": db_relation_complete,
        "identity-credentials": identity_credentials_relation_complete,
        "storage-backend": storage_backend_relation,
    }
    return builders[name]


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
# Tests: all relations complete
# ---------------------------------------------------------------------------


class TestAllRelationsComplete:
    """Config-changed with all mandatory relations → bootstrap progresses."""

    def test_all_relations_present(self, ctx, complete_state):
        """With all mandatory relations, charm should proceed past relation checks.

        Note: the charm may still end up in waiting because
        configure_snap performs additional work (snap installation,
        context retrieval, etc.).  The key assertion is that it does
        NOT block on missing relations.
        """
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite all "
                f"mandatory relations present: {status.message}"
            )


# ---------------------------------------------------------------------------
# Tests: non-leader waiting
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

        assert state_out.unit_status.name in (
            "waiting",
            "blocked",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: install event triggers snap installation
# ---------------------------------------------------------------------------


class TestInstallEvent:
    """Install event should trigger snap installation logic."""

    def test_install_event_runs(self, ctx):
        """Install event should not crash with mocked externals."""
        state_in = testing.State(leader=True)
        # Should not raise — snap installation is mocked
        state_out = ctx.run(ctx.on.install(), state_in)
        # Status may be maintenance/blocked, but should not error
        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


class TestConfigureSnap:
    """Focused tests for snap configuration payloads."""

    def test_configure_snap_sets_ca_bundle(self, ctx, complete_state):
        """configure_snap should pass the receive-ca-cert bundle to the snap."""
        state_in = testing.State(
            leader=True,
            relations=[
                *complete_state.relations,
                receive_ca_cert_relation(
                    ca="ROOT_CA",
                    chain=["INTERMEDIATE_CA"],
                ),
            ],
            secrets=complete_state.secrets,
        )

        with ctx(ctx.on.config_changed(), state_in) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()
            charm_instance.check_serving_backends = MagicMock()

            charm_instance.configure_snap(MagicMock())

            snap_data = charm_instance.set_snap_data.call_args.args[0]
            assert (
                snap_data["ca.bundle"]
                == base64.b64encode(b"ROOT_CA\nINTERMEDIATE_CA").decode()
            )

    def test_configure_snap_unsets_missing_ca_bundle(
        self, ctx, complete_state
    ):
        """configure_snap should unset ca.bundle when no relation is present."""
        with ctx(ctx.on.config_changed(), complete_state) as mgr:
            charm_instance = mgr.charm
            charm_instance.set_snap_data = MagicMock()
            charm_instance.check_serving_backends = MagicMock()

            charm_instance.configure_snap(MagicMock())

            snap_data = charm_instance.set_snap_data.call_args.args[0]
            assert snap_data["ca.bundle"] is None


# ---------------------------------------------------------------------------
# Helpers: cinder-volume relation and stored state builders
# ---------------------------------------------------------------------------


def cinder_volume_relation(
    remote_app_name: str, backend: str
) -> testing.Relation:
    """cinder-volume provides relation with a backend subordinate."""
    return testing.Relation(
        endpoint="cinder-volume",
        remote_app_name=remote_app_name,
        remote_units_data={0: {"backend": backend}},
    )


def _charm_stored_state(
    backends: list[str] | None = None,
) -> testing.StoredState:
    """Build the charm's stored state with given backends."""
    return testing.StoredState(
        name="_state",
        owner_path="CinderVolumeOperatorCharm",
        content={
            "api_ready": True,
            "backends": backends or [],
            "unit_bootstrapped": True,
        },
    )


# ---------------------------------------------------------------------------
# Tests: backend tracking and removal
# ---------------------------------------------------------------------------


class TestBackendLeaving:
    """Verify correct behavior when backends join and leave."""

    def test_backends_tracked_on_relation_changed(self, ctx):
        """_state.backends is populated when cinder-volume relations have backend data."""
        slow_rel = cinder_volume_relation(
            "cinder-volume-ceph-slow", "ceph.slow"
        )
        fast_rel = cinder_volume_relation(
            "cinder-volume-ceph-fast", "ceph.fast"
        )
        state_in = testing.State(
            leader=True,
            relations=_all_mandatory_relations() + [slow_rel, fast_rel],
            secrets=_all_secrets(),
            stored_states=[_charm_stored_state()],
        )
        state_out = ctx.run(ctx.on.relation_changed(slow_rel), state_in)

        ss = state_out.get_stored_state(
            "_state", owner_path="CinderVolumeOperatorCharm"
        )
        assert sorted(ss.content["backends"]) == sorted(
            ["ceph.slow", "ceph.fast"]
        )
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_backend_removed_on_relation_broken(self, ctx):
        """When a cinder-volume relation breaks, backend is removed and snap.unset called."""
        slow_rel = cinder_volume_relation(
            "cinder-volume-ceph-slow", "ceph.slow"
        )
        fast_rel = cinder_volume_relation(
            "cinder-volume-ceph-fast", "ceph.fast"
        )
        state_in = testing.State(
            leader=True,
            relations=_all_mandatory_relations() + [slow_rel, fast_rel],
            secrets=_all_secrets(),
            stored_states=[
                _charm_stored_state(backends=["ceph.slow", "ceph.fast"])
            ],
        )
        state_out = ctx.run(ctx.on.relation_broken(fast_rel), state_in)

        ss = state_out.get_stored_state(
            "_state", owner_path="CinderVolumeOperatorCharm"
        )
        assert ss.content["backends"] == ["ceph.slow"]

        cinder_volume_snap = charm.snap.SnapCache.return_value["cinder-volume"]
        cinder_volume_snap.unset.assert_any_call("ceph.fast")
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_all_backends_removed_waiting(self, ctx):
        """When all backends leave, charm goes to waiting status."""
        slow_rel = cinder_volume_relation(
            "cinder-volume-ceph-slow", "ceph.slow"
        )
        state_in = testing.State(
            leader=True,
            relations=_all_mandatory_relations() + [slow_rel],
            secrets=_all_secrets(),
            stored_states=[_charm_stored_state(backends=["ceph.slow"])],
        )
        state_out = ctx.run(ctx.on.relation_broken(slow_rel), state_in)

        ss = state_out.get_stored_state(
            "_state", owner_path="CinderVolumeOperatorCharm"
        )
        assert ss.content["backends"] == []

        cinder_volume_snap = charm.snap.SnapCache.return_value["cinder-volume"]
        cinder_volume_snap.unset.assert_any_call("ceph.slow")
        assert state_out.unit_status.name == "waiting"


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
