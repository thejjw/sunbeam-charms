#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for Cinder Ceph operator charm class."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils


class _CinderVolumeCephOperatorCharm(charm.CinderVolumeCephOperatorCharm):
    """Charm wrapper for test usage."""

    openstack_release = "wallaby"

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)


def add_complete_cinder_volume_relation(harness: ops.testing.Harness) -> int:
    """Add a complete cinder-volume relation to the charm."""
    return harness.add_relation(
        "cinder-volume",
        "cinder-volume",
        unit_data={
            "snap-name": "cinder-volume",
        },
    )


class TestCinderCephOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderCephOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeCephOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeCephOperatorCharm,
            container_calls=self.container_calls,
        )
        mock_get_platform = patch(
            "charmhelpers.osplatform.get_platform", return_value="ubuntu"
        )
        mock_get_platform.start()

        self.addCleanup(mock_get_platform.stop)
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        test_utils.add_complete_ceph_relation(self.harness)
        add_complete_cinder_volume_relation(self.harness)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

    def test_ceph_access(self):
        """Test charm provides secret via ceph-access."""
        cinder_volume_snap_mock = MagicMock()
        cinder_volume_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        self.snap.SnapCache.return_value = {
            "cinder-volume": cinder_volume_snap_mock
        }
        self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        test_utils.add_complete_ceph_relation(self.harness)
        add_complete_cinder_volume_relation(self.harness)
        access_rel = self.harness.add_relation(
            "ceph-access", "openstack-hypervisor", unit_data={"oui": "non"}
        )
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )
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
        cinder_volume_snap_mock.set.assert_any_call(
            expect_settings, typed=True
        )
        rel_data = self.harness.get_relation_data(
            access_rel, self.harness.charm.unit.app.name
        )
        self.assertRegex(rel_data["access-credentials"], "^secret:.*")
