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

"""Scenario (state-transition) tests for ovn-central-k8s."""

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

import charm
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
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# ---------------------------------------------------------------------------
# Container / relation builders
# ---------------------------------------------------------------------------

CONTAINER_NAMES = ["ovn-sb-db-server", "ovn-nb-db-server", "ovn-northd"]


def _containers(can_connect: bool = True) -> list[testing.Container]:
    return [
        testing.Container(name=name, can_connect=can_connect)
        for name in CONTAINER_NAMES
    ]


def _certificates_relation() -> testing.Relation:
    return testing.Relation(
        endpoint="certificates",
        remote_app_name="vault",
        remote_app_data={"certificates": "TEST_CERT_LIST"},
        remote_units_data={0: {}},
    )


def _peers_relation() -> testing.PeerRelation:
    return testing.PeerRelation(endpoint="peers")


def _all_relations() -> list:
    return [_certificates_relation(), _peers_relation()]


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_cluster_status() -> MagicMock:
    status = MagicMock()
    status.cluster_id = "test-cluster-id"
    status.is_cluster_leader = True
    return status


def _tls_mocks():
    """Context manager that patches TLS handler ready + update_relation_data."""
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
    """Context manager that patches OVN exec-heavy methods and container config."""
    stack = contextlib.ExitStack()
    stack.enter_context(
        mock.patch.object(
            charm.OVNCentralOperatorCharm,
            "configure_ovn_listener",
        )
    )
    stack.enter_context(
        mock.patch.object(
            charm.OVNCentralOperatorCharm,
            "cluster_status",
            return_value=_mock_cluster_status(),
        )
    )
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
            containers=_containers(can_connect=False),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "integration missing" in state_out.unit_status.message


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
            containers=_containers(can_connect=False),
            relations=relations,
        )
        with _tls_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "integration missing" in state_out.unit_status.message


class TestAllRelationsActive:
    """All mandatory relations present → active (leader)."""

    def test_all_relations(self, ctx):
        """Test all relations."""
        state_in = testing.State(
            leader=True,
            containers=_containers(can_connect=True),
            relations=_all_relations(),
        )
        with _tls_mocks(), _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")


class TestWaitingNonLeader:
    """Non-leader with all relations but leader not ready → waiting."""

    def test_waiting_non_leader(self, ctx):
        """Test waiting non leader."""
        state_in = testing.State(
            leader=False,
            containers=_containers(can_connect=True),
            relations=_all_relations(),
        )
        with _tls_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message


class TestPebbleReady:
    """Pebble-ready with all relations → containers configured."""

    def test_pebble_ready(self, ctx):
        """Test pebble ready."""
        containers = _containers(can_connect=True)
        state_in = testing.State(
            leader=True,
            containers=containers,
            relations=_all_relations(),
        )
        with _tls_mocks(), _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.pebble_ready(containers[0]), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        # All three containers should have pebble layers added
        for name in CONTAINER_NAMES:
            out_container = state_out.get_container(name)
            assert out_container.layers, f"Expected layers in container {name}"

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Test pebble ready without relations blocked."""
        containers = _containers(can_connect=True)
        state_in = testing.State(
            leader=True,
            containers=containers,
        )
        state_out = ctx.run(ctx.on.pebble_ready(containers[0]), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestNonLeaderClusterJoin:
    """Non-leader with peer data set joins the OVN cluster."""

    def test_non_leader_cluster_join(self, ctx):
        """Test non leader cluster join."""
        nb_exec = testing.Exec(
            command_prefix=["bash", "/root/ovn-nb-cluster-join.sh"],
        )
        sb_exec = testing.Exec(
            command_prefix=["bash", "/root/ovn-sb-cluster-join.sh"],
        )
        containers = [
            testing.Container(
                name="ovn-sb-db-server",
                can_connect=True,
                execs={sb_exec},
            ),
            testing.Container(
                name="ovn-nb-db-server",
                can_connect=True,
                execs={nb_exec},
            ),
            testing.Container(
                name="ovn-northd",
                can_connect=True,
            ),
        ]
        peers = testing.PeerRelation(
            endpoint="peers",
            local_app_data={
                "nb_cid": "nbcid",
                "sb_cid": "sbcid",
                "leader_ready": "true",
            },
            peers_data={1: {"bound-hostname": "ovn-central-1"}},
        )
        state_in = testing.State(
            leader=False,
            containers=containers,
            relations=[_certificates_relation(), peers],
        )
        with _tls_mocks(), _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
