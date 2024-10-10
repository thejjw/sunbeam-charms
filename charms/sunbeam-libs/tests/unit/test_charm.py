#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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

"""Tests for sunbeam-libs charm."""

import charm
import ops_sunbeam.test_utils as test_utils


class _SunbeamLibsCharm(charm.SunbeamLibsCharm):
    """Dummy class to satisfy reading proper charmcraft file."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)


class TestSunbeamLibsCharm(test_utils.CharmTestCase):
    """Class for testing sunbeam-libs charm."""

    def setUp(self):
        """Run setup for unit tests."""
        super().setUp(charm, [])
        self.harness = test_utils.get_harness(
            _SunbeamLibsCharm,
            container_calls=self.container_calls,
        )

        self.addCleanup(self.harness.cleanup)

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.harness.begin()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(
            self.harness.charm.seen_events,
            ["PebbleReadyEvent"],
        )
