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

"""ops.testing (state-transition) tests for cinder-volume-ceph.

This charm is a subordinate charm.  The mandatory relations are:
ceph (requires) and cinder-volume (requires, container scope).
"""

import dataclasses
import json

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    peer_relation,
)

from .conftest import (
    ceph_relation_complete,
    ceph_relation_no_broker,
    cinder_volume_relation,
)


class TestAllRelations:
    """Config-changed with all mandatory relations present."""

    def test_all_relations(self, ctx, complete_state):
        """With all mandatory relations, charm should proceed past relation checks."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite all "
                f"mandatory relations present: {status.message}"
            )


class TestCephAccess:
    """Test charm provides secret via ceph-access."""

    def test_ceph_access(self, ctx, complete_relations, mock_snap):
        """Ceph-access relation should receive secret credentials."""
        ceph_access_rel = testing.Relation(
            endpoint="ceph-access",
            remote_app_name="openstack-hypervisor",
            remote_units_data={0: {"oui": "non"}},
        )
        cinder_volume_rel = [
            r for r in complete_relations if r.endpoint == "cinder-volume"
        ][0]
        state_in = testing.State(
            leader=True,
            relations=[
                *complete_relations,
                peer_relation(),
                ceph_access_rel,
            ],
        )
        # Trigger cinder-volume relation-changed so the volume_ready
        # callback fires, which then drives configure_charm end-to-end.
        state_out = ctx.run(
            ctx.on.relation_changed(cinder_volume_rel), state_in
        )

        # Check mandatory relations are all ready
        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message

        # Verify snap settings were applied with correct backend config
        cinder_volume_snap = mock_snap.SnapCache.return_value["cinder-volume"]
        expect_settings = {
            "ceph.cinder-volume-ceph": {
                "volume-backend-name": "cinder-volume-ceph",
                "backend-availability-zone": None,
                "mon-hosts": "192.0.2.2",
                "rbd-pool": "cinder-volume-ceph",
                "rbd-user": "cinder-volume-ceph",
                "rbd-secret-uuid": "unknown",
                "rbd-key": "AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==",
                "auth": "cephx",
                "fsid": None,
            }
        }
        cinder_volume_snap.set.assert_any_call(expect_settings, typed=True)

        # Verify ceph-access relation data contains the secret reference
        out_rel = state_out.get_relation(ceph_access_rel.id)
        assert out_rel.local_app_data.get("access-credentials", "").startswith(
            "secret:"
        )


BACKEND_KEY = "ceph.cinder-volume-ceph"
CEPH_KEY = "AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg=="
FSID_A = "11111111-2222-3333-4444-555555555555"
FSID_B = "66666666-7777-8888-9999-000000000000"


def _cinder_volume_unit_data(state_out) -> dict:
    """Local unit data on the cinder-volume relation (set_ready writes here)."""
    rel = [r for r in state_out.relations if r.endpoint == "cinder-volume"][0]
    return dict(rel.local_unit_data)


def _run_volume_ready(ctx, ceph_rel, config=None):
    """Drive configure_charm end-to-end via cinder-volume relation-changed."""
    cinder_volume_rel = cinder_volume_relation()
    state_in = testing.State(
        leader=True,
        config=config or {},
        relations=[ceph_rel, cinder_volume_rel, peer_relation()],
    )
    return ctx.run(ctx.on.relation_changed(cinder_volume_rel), state_in)


def _backend_settings(mock_snap) -> dict:
    """Merged settings pushed to the snap for this backend."""
    cinder_volume_snap = mock_snap.SnapCache.return_value["cinder-volume"]
    merged: dict = {}
    for call in cinder_volume_snap.set.call_args_list:
        merged.update(call.args[0].get(BACKEND_KEY, {}))
    return merged


def _sent_ops(state_out, ceph_rel) -> list | None:
    """Ops of the broker request we sent, or None if none was sent."""
    raw = state_out.get_relation(ceph_rel.id).local_unit_data.get("broker_req")
    return None if raw is None else json.loads(raw)["ops"]


def _peer_app_data(state_out) -> dict:
    rel = [r for r in state_out.relations if r.endpoint == "peers"][0]
    return dict(rel.local_app_data)


@pytest.fixture()
def check_outcome(monkeypatch):
    """Set what ceph-check returns for a test."""

    def _set(rc=charm.CephCheckResult.OK, fsid=None):
        monkeypatch.setattr(
            charm.CinderVolumeCephOperatorCharm,
            "_run_ceph_check",
            lambda self: charm.CephCheckOutcome(rc, fsid),
        )

    return _set


class TestCreatePoolOptOut:
    """create-pool=false: request a key only, never a pool."""

    def test_default_requests_pool_on_join(self, ctx):
        """Baseline: create-pool=true asks for a pool."""
        ceph_rel = ceph_relation_no_broker()
        state_in = testing.State(leader=True, relations=[ceph_rel])
        state_out = ctx.run(
            ctx.on.relation_joined(ceph_rel, remote_unit=0), state_in
        )
        assert _sent_ops(state_out, ceph_rel)

    def test_empty_request_on_join(self, ctx):
        """create-pool=false sends a request with no ops."""
        ceph_rel = ceph_relation_no_broker()
        state_in = testing.State(
            leader=True, config={"create-pool": False}, relations=[ceph_rel]
        )
        state_out = ctx.run(
            ctx.on.relation_joined(ceph_rel, remote_unit=0), state_in
        )
        assert _sent_ops(state_out, ceph_rel) == []

    def test_switching_off_replaces_pool_request(self, ctx):
        """A pool request sent earlier is replaced by an empty one."""
        from charmhelpers.contrib.storage.linux.ceph import (
            CephBrokerRq,
        )

        old = CephBrokerRq(api_version=1)
        old.add_op_create_replicated_pool(name="cinder-volume-ceph")
        ceph_rel = dataclasses.replace(
            ceph_relation_no_broker(),
            local_unit_data={"broker_req": old.request},
        )
        state_in = testing.State(
            leader=True, config={"create-pool": False}, relations=[ceph_rel]
        )
        state_out = ctx.run(
            ctx.on.relation_joined(ceph_rel, remote_unit=0), state_in
        )
        assert _sent_ops(state_out, ceph_rel) == []

    def test_waits_for_broker_response(self, ctx, mock_snap):
        """Key and monitors alone are not enough: wait for the response."""
        state_out = _run_volume_ready(
            ctx, ceph_relation_no_broker(), config={"create-pool": False}
        )
        assert not _backend_settings(mock_snap)
        assert state_out.unit_status.name == "waiting"

    def test_ready_once_response_arrives(self, ctx, mock_snap):
        """The empty request's response makes the backend configurable."""
        _run_volume_ready(
            ctx, ceph_relation_complete(), config={"create-pool": False}
        )
        assert _backend_settings(mock_snap).get("rbd-key") == CEPH_KEY


class TestClientName:
    """rbd-user follows the provider's client-name when it sends one (P8)."""

    def test_default_is_app_name(self, ctx, mock_snap):
        _run_volume_ready(ctx, ceph_relation_complete())
        assert _backend_settings(mock_snap)["rbd-user"] == "cinder-volume-ceph"

    def test_provider_client_name_used(self, ctx, mock_snap):
        _run_volume_ready(
            ctx, ceph_relation_complete({"client-name": "cinder"})
        )
        assert _backend_settings(mock_snap)["rbd-user"] == "cinder"

    def test_permission_message_names_client(self, ctx, check_outcome):
        """The pool check reports the identity actually in use."""
        check_outcome(charm.CephCheckResult.PERMISSION_DENIED)
        state_out = _run_volume_ready(
            ctx, ceph_relation_complete({"client-name": "cinder"})
        )
        assert "client.cinder not authorized" in state_out.unit_status.message


class TestBrokerRejected:
    """A failed broker response blocks with the provider's message (P14)."""

    def test_rejection_blocks(self, ctx, mock_snap):
        stderr = "ceph-integrator cannot create pools"
        state_out = _run_volume_ready(
            ctx,
            ceph_relation_complete(
                rsp_extra={"exit-code": 1, "stderr": stderr}
            ),
        )
        assert state_out.unit_status.name == "blocked"
        assert (
            f"ceph provider rejected request: {stderr}"
            in state_out.unit_status.message
        )
        assert not _backend_settings(mock_snap)

    def test_rejection_without_stderr(self, ctx):
        state_out = _run_volume_ready(
            ctx, ceph_relation_complete(rsp_extra={"exit-code": 2})
        )
        assert "exit-code 2" in state_out.unit_status.message

    def test_stale_rejection_ignored(self, ctx):
        """A failure for an older request id is not reported."""
        state_out = _run_volume_ready(
            ctx,
            ceph_relation_complete(
                rsp_extra={"exit-code": 1, "request-id": "old", "stderr": "x"}
            ),
        )
        assert state_out.unit_status.name != "blocked"


class TestFsid:
    """The backend stays pinned to one cluster."""

    def test_leader_pins_and_sends_fsid(self, ctx, mock_snap, check_outcome):
        check_outcome(fsid=FSID_A)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert _peer_app_data(state_out)[charm.FSID_PEER_KEY] == FSID_A
        assert _backend_settings(mock_snap)["fsid"] == FSID_A
        assert state_out.unit_status.name != "blocked"

    def test_changed_fsid_blocks(self, ctx, check_outcome):
        check_outcome(fsid=FSID_B)
        cinder_volume_rel = cinder_volume_relation()
        state_in = testing.State(
            leader=True,
            relations=[
                ceph_relation_complete(),
                cinder_volume_rel,
                testing.PeerRelation(
                    endpoint="peers",
                    local_app_data={charm.FSID_PEER_KEY: FSID_A},
                ),
            ],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(cinder_volume_rel), state_in
        )
        assert state_out.unit_status.name == "blocked"
        assert (
            f"ceph cluster fsid changed (expected {FSID_A}, got {FSID_B})"
            in state_out.unit_status.message
        )

    def test_provider_fsid_mismatch_blocks(self, ctx, check_outcome):
        check_outcome(fsid=FSID_B)
        state_out = _run_volume_ready(
            ctx, ceph_relation_complete({"fsid": FSID_A})
        )
        assert state_out.unit_status.name == "blocked"
        assert (
            f"ceph fsid mismatch (provider {FSID_A}, cluster {FSID_B})"
            in state_out.unit_status.message
        )
        assert charm.FSID_PEER_KEY not in _peer_app_data(state_out)

    def test_provider_fsid_sent_before_check(self, ctx, mock_snap):
        """With no pin yet, the provider's fsid goes to the snap."""
        _run_volume_ready(ctx, ceph_relation_complete({"fsid": FSID_A}))
        assert _backend_settings(mock_snap)["fsid"] == FSID_A

    def test_non_leader_does_not_pin(self, ctx, check_outcome):
        check_outcome(fsid=FSID_A)
        cinder_volume_rel = cinder_volume_relation()
        state_in = testing.State(
            leader=False,
            relations=[
                ceph_relation_complete(),
                cinder_volume_rel,
                peer_relation(),
            ],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(cinder_volume_rel), state_in
        )
        assert charm.FSID_PEER_KEY not in _peer_app_data(state_out)

    def test_no_fsid_from_old_snap(self, ctx, check_outcome):
        """rc 0 without an fsid: nothing pinned, not blocked."""
        check_outcome(fsid=None)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert charm.FSID_PEER_KEY not in _peer_app_data(state_out)
        assert state_out.unit_status.name != "blocked"

    def test_relation_broken_clears_pin(self, ctx):
        ceph_rel = ceph_relation_complete()
        state_in = testing.State(
            leader=True,
            relations=[
                ceph_rel,
                cinder_volume_relation(),
                testing.PeerRelation(
                    endpoint="peers",
                    local_app_data={charm.FSID_PEER_KEY: FSID_A},
                ),
            ],
        )
        state_out = ctx.run(ctx.on.relation_broken(ceph_rel), state_in)
        assert charm.FSID_PEER_KEY not in _peer_app_data(state_out)


class TestPoolCheck:
    """Pool verification through the snap's ceph-check."""

    @pytest.fixture()
    def check_rc(self, monkeypatch):
        """Set the ceph-check return code for a test."""

        def _set(rc):
            monkeypatch.setattr(
                charm.CinderVolumeCephOperatorCharm,
                "_run_ceph_check",
                lambda self: charm.CephCheckOutcome(rc),
            )

        return _set

    def test_pool_ok(self, ctx, check_rc):
        """rc 0: not blocked."""
        check_rc(charm.CephCheckResult.OK)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name != "blocked"
        assert _cinder_volume_unit_data(state_out)["backend"] == BACKEND_KEY

    def test_pool_missing_blocks(self, ctx, check_rc):
        """Missing pool: blocked, message names the pool."""
        check_rc(charm.CephCheckResult.POOL_MISSING)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name == "blocked"
        assert (
            "pool 'cinder-volume-ceph' does not exist"
            in state_out.unit_status.message
        )

    def test_permission_denied_blocks(self, ctx, check_rc):
        """Unauthorized identity: blocked, message names identity and pool."""
        check_rc(charm.CephCheckResult.PERMISSION_DENIED)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name == "blocked"
        assert (
            "client.cinder-volume-ceph not authorized for pool "
            "'cinder-volume-ceph'" in state_out.unit_status.message
        )

    def test_unreachable_blocks(self, ctx, check_rc):
        """Unreachable cluster is reported distinctly."""
        check_rc(charm.CephCheckResult.UNREACHABLE)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name == "blocked"
        assert "ceph cluster unreachable" in state_out.unit_status.message
        
    def test_unknown_rc_maps_to_error(self):
        """Codes outside the contract never raise; they become ERROR."""
        assert charm.CephCheckResult(99) is charm.CephCheckResult.ERROR

    def test_unknown_rc_blocks(self, ctx, check_rc):
        """A code the charm does not know still blocks."""
        check_rc(charm.CephCheckResult(99))
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name == "blocked"
        assert "ceph pool check failed" in state_out.unit_status.message


    def test_failed_check_does_not_signal_ready(self, ctx, check_rc):
        """The principal is not told the backend is ready on failure."""
        check_rc(charm.CephCheckResult.POOL_MISSING)
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert "backend" not in _cinder_volume_unit_data(state_out)

    def test_old_snap_skips_check(self, ctx):
        """No ceph-check in the snap (default fixture): not blocked."""
        state_out = _run_volume_ready(ctx, ceph_relation_complete())
        assert state_out.unit_status.name != "blocked"

    def test_update_status_rechecks(self, ctx, monkeypatch):
        """update-status re-runs the check so the backend can converge."""
        calls = []

        def _record(self):
            calls.append(True)
            return charm.CephCheckOutcome(charm.CephCheckResult.OK)

        monkeypatch.setattr(
            charm.CinderVolumeCephOperatorCharm, "_run_ceph_check", _record
        )
        state_in = testing.State(
            leader=True,
            relations=[
                ceph_relation_complete(),
                cinder_volume_relation(),
                peer_relation(),
            ],
            stored_states=[
                testing.StoredState(
                    owner_path="CinderVolumeCephOperatorCharm",
                    name="_state",
                    content={"volume_ready": True},
                )
            ],
        )
        ctx.run(ctx.on.update_status(), state_in)
        assert calls
