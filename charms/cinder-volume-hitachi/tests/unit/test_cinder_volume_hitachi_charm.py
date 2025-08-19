#!/usr/bin/env python3
"""Unit tests for cinder‑volume‑hitachi charm class."""

# Copyright 2025 Canonical Ltd.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import (
    annotations,
)

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm  # The built charm module (src/charm.py)
import ops.testing
import ops_sunbeam.test_utils as test_utils

# ---------------------------------------------------------------------------
# Helper wrappers
# ---------------------------------------------------------------------------


class _CinderVolumeHitachiOperatorCharm(
    charm.CinderVolumeHitachiOperatorCharm
):
    """Wrapper that exposes internals for the test harness."""

    openstack_release = "wallaby"

    def __init__(self, framework):
        self.seen_events: list[str] = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)


def add_complete_cinder_volume_relation(harness: ops.testing.Harness) -> int:
    """Add the *cinder-volume* relation with the expected snap info."""
    return harness.add_relation(
        "cinder-volume",
        "cinder-volume",
        unit_data={"snap-name": "cinder-volume"},
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestCinderVolumeHitachiOperatorCharm(test_utils.CharmTestCase):
    """Test suite for the Hitachi backend operator charm."""

    PATCHES: list[str] = []  # No global patches required

    def setUp(self):
        """Setups the test."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()

        # Patch out snap handling – we only care that .set() is invoked.
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeHitachiOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()

        # Create harness with our wrapper charm
        self.harness = test_utils.get_harness(
            _CinderVolumeHitachiOperatorCharm,
            container_calls=self.container_calls,
        )

        # Make sure tests run on Ubuntu (ops_sunbeam uses this internally)
        mock_get_platform = patch(
            "charmhelpers.osplatform.get_platform", return_value="ubuntu"
        )
        mock_get_platform.start()

        # Mock Juju secret retrieval
        self.mock_secret = Mock()
        self.mock_secret.get_content.return_value = {
            "username": "svcuser",
            "password": "secret",
        }
        self.mock_get_secret = patch.object(
            self.harness.model, "get_secret", return_value=self.mock_secret
        )
        self.mock_get_secret.start()

        self.addCleanup(self.mock_get_secret.stop)
        self.addCleanup(mock_get_platform.stop)
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    # ---------------------------------------------------------------------
    # Tests
    # ---------------------------------------------------------------------

    def test_all_relations_ready(self):
        """Charm reports no missing mandatory relations once related."""
        # Set minimal required backend configuration in model config
        self.harness.update_config(
            {
                "san-ip": "10.0.0.50",
                "san-credentials-secret": "secret:test-creds",
                "hitachi-storage-id": "45000",
                "hitachi-pools": "DP_POOL_01",
            }
        )

        self.harness.begin_with_initial_hooks()

        add_complete_cinder_volume_relation(self.harness)

        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

    def test_backend_configuration_pushed_to_snap(self):
        """Operator passes correct backend stanza to the cinder-volume snap."""
        # Mock the cinder-volume snap inside the charm
        cinder_volume_snap_mock = MagicMock()
        cinder_volume_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        self.snap.SnapCache.return_value = {
            "cinder-volume": cinder_volume_snap_mock
        }

        # Provide backend config
        cfg = {
            "san-ip": "10.0.0.50",
            "san-credentials-secret": "secret:test-creds",
            "hitachi-storage-id": "45000",
            "hitachi-pools": "DP_POOL_01",
            "volume-backend-name": "vsp350",
        }
        self.harness.update_config(cfg)

        self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        add_complete_cinder_volume_relation(self.harness)

        expected_settings = {
            "hitachi.cinder-volume-hitachi": {
                "volume-backend-name": "vsp350",
                "san-ip": "10.0.0.50",
                "san-login": "svcuser",
                "san-password": "secret",
                "hitachi-storage-id": "45000",
                "hitachi-pools": "DP_POOL_01",
                # Defaulted fields should *not* appear
            }
        }
        # Verify that the snap received the config with typed=True
        print(cinder_volume_snap_mock.set.call_args_list)
        cinder_volume_snap_mock.set.assert_any_call(
            expected_settings, typed=True
        )
