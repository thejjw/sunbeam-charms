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

"""Scenario (state-transition) tests for ovn-relay-k8s."""

import contextlib
from pathlib import (
    Path,
)
from unittest import (
    mock,
)
from unittest.mock import (
    MagicMock,
    PropertyMock,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.ovn.container_handlers import (
    OVNPebbleHandler,
)
from ops_sunbeam.relation_handlers import (
    TlsCertificatesHandler,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
    certificates_relation_complete,
    k8s_container,
    mandatory_relations_from_charmcraft,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# ---------------------------------------------------------------------------
# Container / relation builders
# ---------------------------------------------------------------------------

CONTAINER_NAME = "ovsdb-server"


def _container(can_connect: bool = True) -> testing.Container:
    return k8s_container(CONTAINER_NAME, can_connect=can_connect)


def _ovsdb_cms_relation() -> testing.Relation:
    """OVSDB-CMS relation with bound-address in remote unit data."""
    return testing.Relation(
        endpoint="ovsdb-cms",
        remote_app_name="ovn-central",
        remote_units_data={0: {"bound-address": "10.0.0.1"}},
    )


def _all_relations() -> list:
    return [
        _ovsdb_cms_relation(),
        certificates_relation_complete(),
        peer_relation(),
    ]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _lb_mock():
    """Mock KubernetesLoadBalancerHandler (k8s interaction)."""
    lb = MagicMock()
    lb.get_loadbalancer_ip.return_value = "10.27.5.1"
    return mock.patch("charm.KubernetesLoadBalancerHandler", return_value=lb)


def _tls_mocks():
    """Patch TLS handler ready + update_relation_data."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(
            TlsCertificatesHandler,
            "ready",
            new_callable=PropertyMock,
            return_value=True,
        )
    )
    stack.enter_context(
        mock.patch.object(
            TlsCertificatesHandler,
            "update_relation_data",
        )
    )
    return stack


def _heavy_ops_mocks():
    """Patch heavy / external-facing operations."""
    stack = contextlib.ExitStack()
    stack.enter_context(_lb_mock())
    stack.enter_context(_tls_mocks())
    stack.enter_context(
        mock.patch.object(
            OVNPebbleHandler,
            "configure_container",
        )
    )
    return stack


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """No relations at all → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """Test blocked when no relations."""
        state_in = testing.State(
            leader=True,
            containers=[_container(can_connect=False)],
        )
        with _lb_mock():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenRelationMissing:
    """Remove each mandatory relation one at a time → blocked."""

    @pytest.mark.parametrize(
        "missing_relation",
        sorted(MANDATORY_RELATIONS),
        ids=sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_each_relation_missing(self, ctx, missing_relation):
        """Test blocked when each relation missing."""
        relations = [
            r for r in _all_relations() if r.endpoint != missing_relation
        ]
        state_in = testing.State(
            leader=True,
            containers=[_container(can_connect=False)],
            relations=relations,
        )
        with _lb_mock(), _tls_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestAllRelationsActive:
    """All mandatory relations present → active (leader)."""

    def test_all_relations(self, ctx):
        """Test all relations."""
        state_in = testing.State(
            leader=True,
            containers=[_container(can_connect=True)],
            relations=_all_relations(),
        )
        with _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")


class TestWaitingNonLeader:
    """Non-leader with all relations but leader not ready → waiting."""

    def test_waiting_non_leader(self, ctx):
        """Test waiting non leader."""
        state_in = testing.State(
            leader=False,
            containers=[_container(can_connect=True)],
            relations=_all_relations(),
        )
        with _lb_mock(), _tls_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "waiting", "Leader not ready")


class TestPebbleReady:
    """Pebble-ready events."""

    def test_pebble_ready(self, ctx):
        """Test pebble ready."""
        container = _container(can_connect=True)
        state_in = testing.State(
            leader=True,
            containers=[container],
            relations=_all_relations(),
        )
        with _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        out_container = state_out.get_container(CONTAINER_NAME)
        assert (
            out_container.layers
        ), f"Expected layers in container {CONTAINER_NAME}"

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Test pebble ready without relations blocked."""
        container = _container(can_connect=True)
        state_in = testing.State(
            leader=True,
            containers=[container],
        )
        with _lb_mock():
            state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert_unit_status(state_out, "blocked")


class TestSouthboundDbUrl:
    """Verify southbound_db_url uses the load-balancer IP."""

    def test_southbound_db_url(self, ctx):
        """Test southbound db url."""
        state_in = testing.State(
            leader=True,
            containers=[_container(can_connect=True)],
        )
        with _lb_mock():
            ctx.run(ctx.on.action("get-southbound-db-url"), state_in)

        assert ctx.action_results == {"url": "ssl:10.27.5.1:6642"}
