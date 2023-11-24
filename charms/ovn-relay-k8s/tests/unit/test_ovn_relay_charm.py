#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd.
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

"""Tests for OVN relay."""

import charm
import ops_sunbeam.test_utils as test_utils


class _OVNRelayOperatorCharm(charm.OVNRelayOperatorCharm):
    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)


class TestOVNRelayOperatorCharm(test_utils.CharmTestCase):
    """Test OVN relay."""

    PATCHES = [
        "KubernetesServicePatch",
    ]

    def setUp(self):
        """Setup OVN relay tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _OVNRelayOperatorCharm, container_calls=self.container_calls
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("ovsdb-server")
        self.assertEqual(len(self.harness.charm.seen_events), 1)

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.set_leader()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        ovsdb_config_files = [
            "/etc/ovn/cert_host",
            "/etc/ovn/key_host",
            "/etc/ovn/ovn-central.crt",
            "/root/ovn-relay-wrapper.sh",
        ]
        for f in ovsdb_config_files:
            self.check_file("ovsdb-server", f)

    def test_southbound_db_url(self):
        """Return southbound db url."""
        self.assertEqual(
            "ssl:10.0.0.10:6642", self.harness.charm.southbound_db_url
        )
