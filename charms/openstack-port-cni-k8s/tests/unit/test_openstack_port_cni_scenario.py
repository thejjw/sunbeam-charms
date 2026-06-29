#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Scenario (ops.testing state-transition) tests for openstack-port-cni-k8s.

Each test provides an input State, fires an event via ctx.run(), then asserts
on the output State (unit_status, workload_version, etc.).  No Harness, no
imperative setup.

Behavioral / interaction tests (mock-call assertions) live in
test_openstack_port_cni_methods.py.
Path setup and autouse mock fixtures live in conftest.py.
"""

import dataclasses
import unittest.mock as mock
from pathlib import (
    Path,
)

from ops import (
    testing,
)
from ops.manifests import (
    ManifestClientError,
)
from ops_sunbeam.test_utils_scenario import (
    assert_relation_broken_causes_blocked_or_waiting,
    certificate_transfer_relation_complete,
    identity_credentials_relation_empty,
    peer_relation,
    tracing_relation_complete,
)

CHARM_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# TestAllRelations
# ---------------------------------------------------------------------------


class TestAllRelations:
    """With all mandatory relations present the charm reaches active status."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """config-changed with all relations → active."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "active"

    def test_workload_version_not_set(self, ctx, complete_state):
        """Workload version is not set (removed from post_config_setup)."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.workload_version == ""


# ---------------------------------------------------------------------------
# TestMissingRelations
# ---------------------------------------------------------------------------


class TestMissingRelations:
    """Missing mandatory relations prevent the charm from becoming active."""

    def test_waiting_without_identity_credentials(self, ctx):
        """No identity-credentials relation → blocked or waiting."""
        state_in = testing.State(
            leader=True,
            relations=[peer_relation()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_waiting_when_identity_credentials_incomplete(self, ctx):
        """Identity-credentials relation present but no credentials → waiting."""
        state_in = testing.State(
            leader=True,
            relations=[identity_credentials_relation_empty(), peer_relation()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_identity_credentials_broken_causes_waiting(
        self, ctx, complete_state
    ):
        """relation-broken on identity-credentials → blocked or waiting."""
        assert_relation_broken_causes_blocked_or_waiting(
            ctx, complete_state, "identity-credentials"
        )


# ---------------------------------------------------------------------------
# TestLeaderVsNonLeader
# ---------------------------------------------------------------------------


class TestLeaderVsNonLeader:
    """Leader reaches active; non-leader waits for leader readiness."""

    def test_leader_vs_non_leader(self, ctx, complete_state):
        """Leader active (with version message), non-leader waiting."""
        leader_state = dataclasses.replace(complete_state, leader=True)
        out = ctx.run(ctx.on.config_changed(), leader_state)
        assert (
            out.unit_status.name == "active"
        ), f"Leader expected active, got {out.unit_status}"

        non_leader_state = dataclasses.replace(complete_state, leader=False)
        out = ctx.run(ctx.on.config_changed(), non_leader_state)
        assert (
            out.unit_status.name == "waiting"
        ), f"Non-leader expected waiting, got {out.unit_status}"


# ---------------------------------------------------------------------------
# TestManifestErrors
# ---------------------------------------------------------------------------


class TestManifestErrors:
    """ManifestClientError from the k8s API surfaces as WaitingStatus."""

    def test_waiting_when_ovs_cni_apply_fails(self, ctx, complete_state):
        """ovs-cni apply_manifests raising ManifestClientError → waiting."""
        with mock.patch(
            "ops.manifests.manifest.Manifests.apply_manifests",
            side_effect=ManifestClientError("connection refused"),
        ):
            state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "waiting"


# ---------------------------------------------------------------------------
# TestOptionalRelations
# ---------------------------------------------------------------------------


class TestOptionalRelations:
    """Optional relations (tracing, receive-ca-cert) do not block the charm."""

    def test_active_with_tracing_relation(
        self, ctx, complete_relations, complete_secrets
    ):
        """Tracing relation present and complete → still active."""
        state_in = testing.State(
            leader=True,
            relations=list(complete_relations) + [tracing_relation_complete()],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "active"

    def test_active_with_ca_cert_relation(
        self, ctx, complete_relations, complete_secrets
    ):
        """receive-ca-cert relation present and complete → still active."""
        state_in = testing.State(
            leader=True,
            relations=list(complete_relations)
            + [certificate_transfer_relation_complete()],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name == "active"


# ---------------------------------------------------------------------------
# TestPodRestart
# ---------------------------------------------------------------------------


def _bootstrapped_state() -> testing.StoredState:
    """Return StoredState for OpenstackPortCniCharm with unit_bootstrapped=True.

    Mirrors the charm's ``_state`` after a successful bootstrap. On a pod
    replace local unit storage is lost, so the charm sees
    ``unit_bootstrapped=False`` again (the default).
    """
    return testing.StoredState(
        name="_state",
        owner_path="OpenstackPortCniCharm",
        content={"unit_bootstrapped": True},
    )


class TestPodRestart:
    """A pod restart drops local unit storage and must not leave the unit stuck.

    On K8s, ``unit_bootstrapped`` lives in local unit storage (on the pod
    filesystem). When the pod is replaced (e.g. a node restart reschedules
    it), that storage is lost and the flag reverts to False, so the charm
    re-enters ``maintenance (bootstrap) Service not bootstrapped``. After the
    restart juju only fires ``start``; no relation/config event is emitted
    unless relation data actually changes. ``_on_start`` must therefore
    re-run ``configure_charm`` to reconcile the bootstrap status.
    """

    def test_start_after_pod_restart_rebootstraps(self, ctx, complete_state):
        """Start with lost unit_bootstrapped → active (re-bootstrap)."""
        # Simulate the post-restart state: relations/secrets intact, but local
        # unit storage (unit_bootstrapped) lost back to its default of False.
        state_in = dataclasses.replace(
            complete_state,
            stored_states={
                testing.StoredState(
                    name="_state",
                    owner_path="OpenstackPortCniCharm",
                    content={"unit_bootstrapped": False},
                )
            },
        )
        state_out = ctx.run(ctx.on.start(), state_in)
        assert (
            state_out.unit_status.name == "active"
        ), f"Expected active after start re-bootstrap, got {state_out.unit_status}"

    def test_start_preserves_bootstrapped_state(self, ctx, complete_state):
        """Start on an already-bootstrapped unit → stays active."""
        state_in = dataclasses.replace(
            complete_state,
            stored_states={_bootstrapped_state()},
        )
        state_out = ctx.run(ctx.on.start(), state_in)
        assert (
            state_out.unit_status.name == "active"
        ), f"Expected active, got {state_out.unit_status}"

    def test_config_changed_bootstraps_unit(self, ctx, complete_state):
        """Config-changed sets unit_bootstrapped=True on a fresh unit."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "active"
        state = next(
            s
            for s in state_out.stored_states
            if s.owner_path == "OpenstackPortCniCharm" and s.name == "_state"
        )
        assert state.content.get("unit_bootstrapped") is True
