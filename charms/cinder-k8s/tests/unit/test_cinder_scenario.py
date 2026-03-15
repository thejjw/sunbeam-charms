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

"""Scenario (ops.testing state-transition) tests for cinder-k8s."""

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
    assert_config_file_not_contains,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    k8s_api_container,
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

    def test_config_file_written(self, ctx, complete_state):
        """All relations present → cinder.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "cinder-api", "/etc/cinder/cinder.conf"
        )

    def test_scheduler_config_file_written(self, ctx, complete_state):
        """All relations present → cinder.conf is rendered in scheduler container."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "cinder-scheduler", "/etc/cinder/cinder.conf"
        )

    def test_cinder_conf_contains_os_region_name(self, ctx, complete_state):
        """cinder.conf includes os_region_name from the region config option."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "cinder-api",
            "/etc/cinder/cinder.conf",
            ["os_region_name = RegionOne"],
        )

    def test_cinder_conf_contains_peer_client_sections(
        self, ctx, complete_state
    ):
        """cinder.conf renders explicit client sections for peer services."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "cinder-api",
            "/etc/cinder/cinder.conf",
            [
                "[glance]",
                "auth_section = keystone_authtoken",
                "valid_interfaces = admin",
                "[nova]",
                "auth_section = service_user",
                "interface = admin",
                "[barbican]",
                "auth_section = keystone_authtoken",
            ],
        )

    def test_wsgi_cinder_conf_contains_heartbeat_in_pthread(
        self, ctx, complete_state
    ):
        """cinder-api should render heartbeat_in_pthread."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_contains(
            state_out,
            ctx,
            "cinder-api",
            "/etc/cinder/cinder.conf",
            ["heartbeat_in_pthread = True"],
        )

    def test_scheduler_cinder_conf_omits_heartbeat_in_pthread(
        self, ctx, complete_state
    ):
        """cinder-scheduler should not render heartbeat_in_pthread."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_not_contains(
            state_out,
            ctx,
            "cinder-scheduler",
            "/etc/cinder/cinder.conf",
            ["heartbeat_in_pthread"],
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_api(self, ctx, complete_state):
        """Pebble-ready on cinder-api adds a layer and starts the WSGI service."""
        container = complete_state.get_container("cinder-api")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("cinder-api")
        assert "cinder-api" in out_container.layers
        layer = out_container.layers["cinder-api"]
        assert "wsgi-cinder-api" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("wsgi-cinder-api") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        api = k8s_api_container(
            "cinder-api",
            extra_execs=[
                testing.Exec(command_prefix=["a2disconf"], return_code=0),
            ],
        )
        scheduler = k8s_container("cinder-scheduler")
        state_in = testing.State(leader=True, containers=[api, scheduler])
        state_out = ctx.run(ctx.on.pebble_ready(api), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        api = k8s_api_container("cinder-api", can_connect=False)
        scheduler = k8s_container("cinder-scheduler", can_connect=False)
        state_in = testing.State(leader=True, containers=[api, scheduler])
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", "integration missing")


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
        all_containers,
        missing_rel,
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=all_containers,
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
        self, ctx, complete_relations, complete_secrets, all_containers
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=all_containers,
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
