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

"""Scenario (ops.testing state-transition) tests for magnum-k8s."""

import sys
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
    k8s_api_container,
    k8s_container,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# conftest.py is auto-loaded by pytest but not directly importable;
# ensure its directory is on sys.path so we can reuse factory functions.
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from conftest import (  # noqa: E402
    KUBECONFIG_SECRET_ID,
    kubeconfig_secret,
    magnum_peer_relation,
)


# identity-ops is tested separately because its readiness depends on
# peer data rather than the relation itself.
class TestAllRelations:
    """With all relations complete the charm reaches active and configures the service."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_magnum_conf_written(self, ctx, complete_state):
        """All relations present → magnum.conf is rendered in magnum-api."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "magnum-api", "/etc/magnum/magnum.conf"
        )

    def test_wsgi_site_config_written(self, ctx, complete_state):
        """Apache WSGI site config is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out,
            ctx,
            "magnum-api",
            "/etc/apache2/sites-available/wsgi-magnum-api.conf",
        )

    def test_magnum_conf_uses_admin_client_endpoints(
        self, ctx, complete_state
    ):
        """Peer client groups in magnum.conf prefer admin URLs."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "magnum-api",
            "/etc/magnum/magnum.conf",
            [
                "[cinder_client]",
                "endpoint_type = adminURL",
                "[glance_client]",
                "[heat_client]",
                "[neutron_client]",
                "[nova_client]",
                "[octavia_client]",
            ],
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_api(self, ctx, complete_state):
        """Pebble-ready on magnum-api with all relations → active."""
        container = complete_state.get_container("magnum-api")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_pebble_ready_conductor(self, ctx, complete_state):
        """Pebble-ready on magnum-conductor with all relations → active."""
        container = complete_state.get_container("magnum-conductor")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        api = k8s_api_container("magnum-api")
        conductor = k8s_container("magnum-conductor")
        state_in = testing.State(
            leader=True,
            containers=[api, conductor],
            config={"kubeconfig": KUBECONFIG_SECRET_ID},
            secrets=[kubeconfig_secret()],
        )
        state_out = ctx.run(ctx.on.pebble_ready(api), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        api = k8s_api_container("magnum-api", can_connect=False)
        conductor = k8s_container("magnum-conductor", can_connect=False)
        state_in = testing.State(
            leader=True,
            containers=[api, conductor],
            config={"kubeconfig": KUBECONFIG_SECRET_ID},
            secrets=[kubeconfig_secret()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

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
            config={"kubeconfig": KUBECONFIG_SECRET_ID},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )

    def test_blocked_when_identity_ops_missing(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        containers,
    ):
        """Charm should be blocked/waiting when identity-ops is absent."""
        # Remove identity-ops relation AND replace peer with one lacking
        # config credentials so the handler reports not-ready.
        remaining = [
            r
            for r in complete_relations
            if r.endpoint not in ("identity-ops", "peers")
        ]
        remaining.append(magnum_peer_relation(config_secret_id=""))
        # Remove the identity-ops config secret from secrets as well
        remaining_secrets = [
            s
            for s in complete_secrets
            if s.label != "configure-credential-magnum_domain_admin"
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=containers,
            secrets=remaining_secrets,
            config={"kubeconfig": KUBECONFIG_SECRET_ID},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when 'identity-ops' missing, "
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
            config={"kubeconfig": KUBECONFIG_SECRET_ID},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.WaitingStatus)


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
