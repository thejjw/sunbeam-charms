#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
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

"""Unit tests for Cinder infinidat operator charm."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils


class _CinderVolumeInfinidatOperatorCharm(charm.CinderVolumeInfinidatOperatorCharm):
    """Charm wrapper for test usage."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)
        self._snap = Mock()

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def get_snap(self):
        return self._snap


def add_complete_cinder_volume_relation(harness: ops.testing.Harness) -> int:
    """Add a complete cinder-volume relation to the charm."""
    return harness.add_relation(
        "cinder-volume",
        "cinder-volume",
        unit_data={
            "snap-name": "cinder-volume",
        },
    )


class TestCinderInfinidatOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeInfinidatOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeInfinidatOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeInfinidatOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        # Add secret for the secret-type config field
        secret_login = self.harness.add_user_secret({"san-login": "test-login"})
        secret_password = self.harness.add_user_secret({"san-password": "test-password"})
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(secret_login, self.harness.charm.app)
        self.harness.grant_secret(secret_password, self.harness.charm.app)
        # Update config with required fields and the secret reference
        self.harness.update_config(
            {"san-ip": "10.20.20.3", "san-login": secret_login, "san-password": secret_password}
        )
        self.harness.evaluate_status()
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )
