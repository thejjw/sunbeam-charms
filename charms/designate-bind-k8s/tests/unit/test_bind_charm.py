# Copyright 2023 Canonical Ltd.
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

"""Unit tests."""

import charm
import ops_sunbeam.test_utils as test_utils


class _BindTestOperatorCharm(charm.BindOperatorCharm):
    """Test Operator Charm for Bind Operator."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        """Configure charm."""
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        return "bind.juju"


class TestBindOperatorCharm(test_utils.CharmTestCase):
    """Test charm."""

    PATCHES = []

    def setUp(self):
        """Test setup."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _BindTestOperatorCharm, container_calls=self.container_calls
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 1)

    def test_peer_relation(self):
        """Test peer integration for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        test_utils.add_complete_peer_relation(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 3)
