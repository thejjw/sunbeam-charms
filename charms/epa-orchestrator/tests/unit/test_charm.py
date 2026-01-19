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

import charm
import ops_sunbeam.test_utils as test_utils


class _EpaOrchestratorCharm(charm.EpaOrchestratorCharm):
    """EPA Orchestrator test charm."""

    def __init__(self, framework):
        super().__init__(framework)


class TestEpaOrchestratorCharm(test_utils.CharmTestCase):
    """Test charm to test EPA Orchestrator charm."""

    PATCHES = []

    def setUp(self):
        """Setup EPA Orchestrator tests."""
        super().setUp(charm, self.PATCHES)

        self.harness = test_utils.get_harness(
            _EpaOrchestratorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Setting up configuration."""
        self.harness.update_config(
            {"snap-name": "epa-orchestrator", "snap-channel": "latest/edge"}
        )
        self.harness.begin_with_initial_hooks()

    def test_snap_name_property_default(self):
        """Test snap_name property returns default value."""
        self.harness.begin()

        self.assertEqual(self.harness.charm.snap_name, "epa-orchestrator")

    def test_snap_channel_property(self):
        """Test snap_channel property returns configured channel."""
        self.harness.begin()
        self.harness.update_config({"snap-channel": "latest/edge"})

        self.assertEqual(self.harness.charm.snap_channel, "latest/edge")

    def test_ensure_services_running_no_op(self):
        """Test ensure_services_running is a no-op and doesn't raise errors."""
        self.harness.begin()

        # This should not raise any exceptions
        result = self.harness.charm.ensure_services_running(False)

        # Should return None (no-op)
        self.assertIsNone(result)

    def test_stop_services_no_op(self):
        """Test stop_services is a no-op and doesn't raise errors."""
        self.harness.begin()

        # This should not raise any exceptions
        result = self.harness.charm.stop_services()

        # Should return None (no-op)
        self.assertIsNone(result)

    def test_all_relations(self):
        """Test that all relation handlers are properly configured."""
        self.harness.begin()

        self.assertFalse(self.harness.charm.sunbeam_machine.ready)
        self.harness.add_relation("sunbeam-machine", "sunbeam-machine")
        self.assertTrue(self.harness.charm.sunbeam_machine.ready)
