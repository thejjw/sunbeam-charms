# Copyright 2023 Canonical Ltd.
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

"""Tests for EPA Orchestrator charm."""

from unittest.mock import (
    MagicMock,
    patch,
)

import charm
import charms.operator_libs_linux.v2.snap as snap
import ops_sunbeam.test_utils as test_utils


class _EpaOrchestratorCharm(charm.EpaOrchestratorCharm):
    """EPA Orchestrator test charm."""

    def __init__(self, framework):
        super().__init__(framework)


class TestEpaOrchestratorCharm(test_utils.CharmTestCase):
    """Test charm to test EPA Orchestrator charm."""

    PATCHES = ["snap"]

    def setUp(self):
        """Setup EPA Orchestrator tests."""
        super().setUp(charm, self.PATCHES)

        self.snap.SnapError = snap.SnapError
        self.snap.SnapNotFoundError = snap.SnapNotFoundError
        self.snap.SnapState = snap.SnapState

        self.harness = test_utils.get_harness(
            _EpaOrchestratorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Setting up configuration."""
        self.harness.update_config({"snap-channel": "latest/edge"})
        self.harness.begin_with_initial_hooks()

    def test_snap_not_installed(self):
        """Check action raises SnapInstallationError when SnapNotFoundError occurs."""
        self.harness.begin()
        self.snap.SnapCache.side_effect = snap.SnapNotFoundError(
            "Snap not found"
        )
        with self.assertRaises(charm.SnapInstallationError) as cm:
            self.harness.charm.ensure_snap_epa_orchestrator()

            self.assertIn(
                "Snap epa-orchestrator failed. Reason: Snap not found",
                str(cm.exception),
            )

    def test_snap_installation_error(self):
        """Check that SnapInstallationError is raised when snap installation fails."""
        self.harness.begin()

        mock_cache = MagicMock()
        mock_snap = MagicMock()
        mock_snap.present = False

        snap_error = snap.SnapError("Installation failed")
        mock_snap.ensure.side_effect = snap_error
        mock_cache.__getitem__.return_value = mock_snap

        self.snap.SnapCache.return_value = mock_cache

        with self.assertRaises(charm.SnapInstallationError) as cm:
            self.harness.charm.ensure_snap_epa_orchestrator()

        self.assertIn(
            "Snap epa-orchestrator failed. Reason: Installation failed",
            str(cm.exception),
        )

    def test_snap_already_installed(self):
        """Check that snap installation is skipped if already present."""
        self.harness.begin()

        mock_cache = MagicMock()
        mock_snap = MagicMock()
        mock_snap.present = True
        mock_cache.__getitem__.return_value = mock_snap

        self.snap.SnapCache.return_value = mock_cache

        self.harness.charm.ensure_snap_epa_orchestrator()

        mock_snap.ensure.assert_not_called()

    def test_snap_installation_success(self):
        """Check successful snap installation."""
        self.harness.begin()

        mock_cache = MagicMock()
        mock_snap = MagicMock()
        mock_snap.present = False
        mock_cache.__getitem__.return_value = mock_snap

        self.snap.SnapCache.return_value = mock_cache

        self.harness.charm.ensure_snap_epa_orchestrator()

        mock_snap.ensure.assert_called_once_with(
            snap.SnapState.Latest,
            channel=self.harness.charm.model.config.get("snap-channel"),
        )

    def test_configure_unit(self):
        """Test configure_unit method."""
        self.harness.begin()

        with patch.object(self.harness.charm, "check_leader_ready"):
            with patch.object(
                self.harness.charm, "ensure_snap_epa_orchestrator"
            ):
                event = MagicMock()
                self.harness.charm.configure_unit(event)

                self.assertTrue(self.harness.charm._state.unit_bootstrapped)

    def test_configure_unit_calls_ensure_snap(self):
        """Test that configure_unit calls ensure_snap_epa_orchestrator."""
        self.harness.begin()

        with patch.object(self.harness.charm, "check_leader_ready"):
            with patch.object(
                self.harness.charm, "ensure_snap_epa_orchestrator"
            ) as mock_ensure:
                event = MagicMock()
                self.harness.charm.configure_unit(event)

                mock_ensure.assert_called_once()

    def test_get_snap_cache(self):
        """Test get_snap_cache method returns SnapCache."""
        self.harness.begin()

        result = self.harness.charm.get_snap_cache()

        self.snap.SnapCache.assert_called_once()
        self.assertEqual(result, self.snap.SnapCache.return_value)
