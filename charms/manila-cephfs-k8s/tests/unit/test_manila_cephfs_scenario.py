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

"""Scenario (ops.testing state-transition) tests for manila-cephfs-k8s."""

from pathlib import (
    Path,
)

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

    def test_manila_conf_written(self, ctx, complete_state):
        """All relations present → manila.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "manila-share", "/etc/manila/manila.conf"
        )

    def test_ceph_conf_written(self, ctx, complete_state):
        """All relations present → ceph.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "manila-share", "/etc/ceph/ceph.conf"
        )

    def test_ceph_keyring_written(self, ctx, complete_state):
        """All relations present → manila.keyring is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "manila-share", "/etc/ceph/manila.keyring"
        )

    def test_manila_conf_contents(self, ctx, complete_state):
        """manila.conf contains expected cephfs backend sections."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "manila-share",
            "/etc/manila/manila.conf",
            [
                "enabled_share_backends = cephnfs",
                "[cephnfs]",
                "share_backend_name = CEPHNFS",
                "cephfs_auth_id = foo",
                "cephfs_cluster_name = lish",
                "cephfs_nfs_cluster_id = lish",
                "cephfs_filesystem_name = voly",
            ],
        )

    def test_ceph_conf_contents(self, ctx, complete_state):
        """ceph.conf contains expected mon host and keyring path."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "manila-share",
            "/etc/ceph/ceph.conf",
            [
                "[global]",
                "mon host = mony",
                "[client]",
                "keyring = /etc/ceph/manila.keyring",
            ],
        )

    def test_ceph_keyring_contents(self, ctx, complete_state):
        """manila.keyring contains client id and key."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "manila-share",
            "/etc/ceph/manila.keyring",
            [
                "[client.foo]",
                "key = keys-do-not-ring",
            ],
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the manila-share service."""
        container = complete_state.get_container("manila-share")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("manila-share")
        assert "manila-share" in out_container.layers
        layer = out_container.layers["manila-share"]
        assert "manila-share" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("manila-share") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        container = k8s_container("manila-share")
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        container = k8s_container("manila-share", can_connect=False)
        state_in = testing.State(leader=True, containers=[container])
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


class TestRelationRemoval:
    """Relation broken events are handled without errors."""

    def test_ceph_nfs_removal_blocks_charm(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Removing ceph-nfs via relation-broken → blocked/waiting."""
        ceph_nfs = [r for r in complete_relations if r.endpoint == "ceph-nfs"][
            0
        ]
        remaining = [r for r in complete_relations if r.endpoint != "ceph-nfs"]
        state_in = testing.State(
            leader=True,
            relations=[*remaining, ceph_nfs],
            containers=[container],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.relation_broken(ceph_nfs), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_manila_removal_handled(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Removing manila via relation-broken does not crash the charm."""
        manila = testing.Relation(
            endpoint="manila",
            remote_app_name="manila",
        )
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, manila],
            containers=[container],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.relation_broken(manila), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


class TestNonLeader:
    """Non-leader behaviour (no peers relation → leader readiness assumed)."""

    def test_non_leader_active_without_peers(
        self, ctx, complete_relations, complete_secrets, container
    ):
        """Non-leader unit reaches active when no peers relation exists.

        This charm has no peers relation, so leader-readiness checks are
        skipped and the non-leader unit is allowed to go active.
        """
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")


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
