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
import ops
import pytest
from ops import (
    testing,
)
from ops_sunbeam.ovn.config_contexts import (
    OVNDBConfigContext,
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
        peers = _peers_relation()
        state_in = testing.State(
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with _tls_mocks(), _heavy_ops_mocks():
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        # The bootstrap leader is a cluster member too.
        assert (
            state_out.get_relation(peers.id).local_unit_data["cluster_member"]
            == "true"
        )


class TestClusterMemberContext:
    """Cluster membership peer data is exposed to template contexts."""

    @pytest.mark.parametrize(
        ("unit_data", "expected"),
        [({}, False), ({"cluster_member": "true"}, True)],
    )
    def test_unit_cluster_member_context(self, ctx, unit_data, expected):
        """The context reflects this unit's peer relation marker."""
        peers = testing.PeerRelation(
            endpoint="peers",
            local_app_data={
                "nb_cid": "nbcid",
                "sb_cid": "sbcid",
                "leader_ready": "true",
            },
            local_unit_data=unit_data,
        )
        state_in = testing.State(
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )

        with _tls_mocks(), _heavy_ops_mocks():
            with ctx(ctx.on.config_changed(), state_in) as mgr:
                context = OVNDBConfigContext(mgr.charm, "ovs_db").context()

        assert context["is_unit_cluster_member"] is expected


class TestClusterMemberUpgradeBackfill:
    """Upgrade marks already-running DB services as existing members."""

    def test_backfills_flag_when_both_db_services_are_running(self, ctx):
        """Existing running DB services receive the peer marker on upgrade."""
        containers = _containers(can_connect=True)
        peers = _peers_relation()
        state_in = testing.State(
            leader=True,
            containers=containers,
            relations=[_certificates_relation(), peers],
        )

        with (
            _tls_mocks(),
            mock.patch.object(
                TlsCertificatesHandler,
                "validate_and_regenerate_certificates_if_needed",
            ),
            mock.patch.object(
                ops.model.Container,
                "get_services",
                return_value={
                    "ovn-sb-db-server": MagicMock(is_running=lambda: True),
                    "ovn-nb-db-server": MagicMock(is_running=lambda: True),
                },
            ),
        ):
            state_out = ctx.run(ctx.on.upgrade_charm(), state_in)

        assert (
            state_out.get_relation(peers.id).local_unit_data["cluster_member"]
            == "true"
        )

    def test_does_not_backfill_when_db_service_is_not_running(self, ctx):
        """A unit without both running DB services remains unmarked."""
        containers = _containers(can_connect=True)
        peers = _peers_relation()
        state_in = testing.State(
            leader=True,
            containers=containers,
            relations=[_certificates_relation(), peers],
        )

        with (
            _tls_mocks(),
            mock.patch.object(
                TlsCertificatesHandler,
                "validate_and_regenerate_certificates_if_needed",
            ),
            mock.patch.object(
                ops.model.Container,
                "get_services",
                return_value={
                    "ovn-sb-db-server": MagicMock(is_running=lambda: True),
                    "ovn-nb-db-server": MagicMock(is_running=lambda: False),
                },
            ),
        ):
            state_out = ctx.run(ctx.on.upgrade_charm(), state_in)

        assert (
            "cluster_member"
            not in state_out.get_relation(peers.id).local_unit_data
        )


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
        assert (
            state_out.get_relation(peers.id).local_unit_data["cluster_member"]
            == "true"
        )


class TestPeerDeparted:
    """Departing peers are removed from the OVN RAFT clusters."""

    _SCHEMAS = {"ovnnb_db": "OVN_Northbound", "ovnsb_db": "OVN_Southbound"}

    def _cluster_status_for(self, hostname: str):
        # cluster_kick derives the RAFT schema from status.name, so return a
        # status whose name matches the queried target.
        def _status(target, cmd_executor=None) -> MagicMock:
            status = MagicMock()
            status.name = self._SCHEMAS[target]
            status.servers = [
                ("aaaa", "ssl:ovn-central-0:6643"),
                ("bbbb", f"ssl:{hostname}:6643"),
            ]
            return status

        return _status

    def test_leader_kicks_departing_peer(self, ctx):
        """Leader kicks the departing server as a fallback if it did not leave."""
        departing_hostname = "ovn-central-1"
        peers = testing.PeerRelation(
            endpoint="peers",
            local_app_data={
                "nb_cid": "nbcid",
                "sb_cid": "sbcid",
                "leader_ready": "true",
            },
            peers_data={1: {"bound-hostname": departing_hostname}},
        )
        state_in = testing.State(
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "_wait_for_server_leave",
                return_value=False,
            ),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                side_effect=self._cluster_status_for(departing_hostname),
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            ctx.run(
                ctx.on.relation_departed(peers, remote_unit=1),
                state_in,
            )

        kicked = [
            (call.args[0], call.args[1]) for call in ovn_appctl.call_args_list
        ]
        assert (
            "ovnnb_db",
            ("cluster/kick", "OVN_Northbound", "bbbb"),
        ) in kicked
        assert (
            "ovnsb_db",
            ("cluster/kick", "OVN_Southbound", "bbbb"),
        ) in kicked
        # The surviving server must not be kicked.
        for _, args in kicked:
            assert args[2] != "aaaa"

    def test_non_leader_does_not_kick(self, ctx):
        """A non-leader unit does not attempt to remove departing peers."""
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
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with (
            _tls_mocks(),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            ctx.run(
                ctx.on.relation_departed(peers, remote_unit=1),
                state_in,
            )

        ovn_appctl.assert_not_called()

    def test_leader_skips_kick_when_peer_left(self, ctx):
        """No kick is issued when the departing server already left."""
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
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "_wait_for_server_leave",
                return_value=True,
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            ctx.run(
                ctx.on.relation_departed(peers, remote_unit=1),
                state_in,
            )

        ovn_appctl.assert_not_called()

    def test_leader_kick_matches_exact_hostname(self, ctx):
        """Kicking 'ovn-central-1' must not match 'ovn-central-10'."""
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
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )

        def _status(target, cmd_executor=None) -> MagicMock:
            status = MagicMock()
            status.name = self._SCHEMAS[target]
            status.servers = [
                ("aaaa", "ssl:ovn-central-0:6643"),
                ("cccc", "ssl:ovn-central-10:6643"),
                ("bbbb", "ssl:ovn-central-1:6643"),
            ]
            return status

        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "_wait_for_server_leave",
                return_value=False,
            ),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                side_effect=_status,
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            ctx.run(
                ctx.on.relation_departed(peers, remote_unit=1),
                state_in,
            )

        kicked_ids = [call.args[1][2] for call in ovn_appctl.call_args_list]
        assert kicked_ids
        assert "bbbb" in kicked_ids
        assert "aaaa" not in kicked_ids
        assert "cccc" not in kicked_ids

    def test_leave_wait_ignores_similar_hostnames(self, ctx):
        """'ovn-central-10' remaining does not block 'ovn-central-1' leave."""
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
            leader=True,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )

        def _status(target, cmd_executor=None) -> MagicMock:
            status = MagicMock()
            status.name = self._SCHEMAS[target]
            status.servers = [
                ("aaaa", "ssl:ovn-central-0:6643"),
                ("cccc", "ssl:ovn-central-10:6643"),
            ]
            return status

        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                side_effect=_status,
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            ctx.run(
                ctx.on.relation_departed(peers, remote_unit=1),
                state_in,
            )

        # ovn-central-1 is considered gone, so no kick is issued.
        ovn_appctl.assert_not_called()

    def test_leave_wait_tolerates_status_errors(self, ctx):
        """Transient status errors keep polling instead of aborting the wait."""
        peers = testing.PeerRelation(
            endpoint="peers",
            peers_data={1: {"bound-hostname": "ovn-central-1"}},
        )
        state_in = testing.State(
            leader=False,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        predicates = []

        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                side_effect=ops.pebble.ExecError(
                    ["cluster/status"], 1, "", ""
                ),
            ),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "_poll_until",
                side_effect=lambda predicate: predicates.append(predicate)
                or False,
            ),
        ):
            with ctx(
                ctx.on.relation_departed(peers, remote_unit=1), state_in
            ) as mgr:
                result = mgr.charm._wait_for_server_leave(
                    "nb", "ovn-central-1"
                )

        assert result is False
        # The predicate reports False on transient errors so polling
        # continues until the timeout rather than the wait aborting (and
        # silently skipping the fallback kick).
        assert predicates
        assert predicates[0]() is False


class TestClusterLeave:
    """A departing unit gracefully leaves the OVN RAFT clusters."""

    def _member_status(self, members: int) -> MagicMock:
        # short id "cccc" is absent from the member list, so the leave-wait
        # completes immediately in tests.
        status = MagicMock()
        status.server_id = "cccc0000-0000-0000-0000-000000000000"
        status.name = "OVN_Cluster"
        status.servers = [
            (short, f"ssl:ovn-central-{i}:6643")
            for i, short in enumerate(["aaaa", "bbbb", "dddd"][:members])
        ]
        return status

    def _departed_peers(self) -> testing.PeerRelation:
        return testing.PeerRelation(
            endpoint="peers",
            peers_data={1: {"bound-hostname": "ovn-central-1"}},
        )

    def test_cluster_leave_multi_member(self, ctx):
        """cluster_leave issues cluster/leave when part of a cluster."""
        peers = self._departed_peers()
        state_in = testing.State(
            leader=False,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                return_value=self._member_status(2),
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            with ctx(
                ctx.on.relation_departed(peers, remote_unit=1), state_in
            ) as mgr:
                mgr.charm.cluster_leave("nb")

        assert any(
            call.args[0] == "ovnnb_db" and call.args[1][0] == "cluster/leave"
            for call in ovn_appctl.call_args_list
        )

    def test_cluster_leave_single_member(self, ctx):
        """A single-member cluster has nothing to leave."""
        peers = self._departed_peers()
        state_in = testing.State(
            leader=False,
            containers=_containers(can_connect=True),
            relations=[_certificates_relation(), peers],
        )
        with (
            _tls_mocks(),
            mock.patch.object(
                charm.OVNCentralOperatorCharm,
                "cluster_status",
                return_value=self._member_status(1),
            ),
            mock.patch.object(charm.ovn, "ovn_appctl") as ovn_appctl,
        ):
            with ctx(
                ctx.on.relation_departed(peers, remote_unit=1), state_in
            ) as mgr:
                mgr.charm.cluster_leave("nb")

        ovn_appctl.assert_not_called()
