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

from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    peer_relation,
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
            }
        }
        cinder_volume_snap.set.assert_any_call(expect_settings, typed=True)

        # Verify ceph-access relation data contains the secret reference
        out_rel = state_out.get_relation(ceph_access_rel.id)
        assert out_rel.local_app_data.get("access-credentials", "").startswith(
            "secret:"
        )
