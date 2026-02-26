#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd.
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

"""Unit tests for core Cinder charm class."""

import charm
import ops_sunbeam.test_utils as test_utils


class _CinderOperatorCharm(charm.CinderOperatorCharm):
    """Test implementation of Cinder operator."""

    def __init__(self, framework):
        self.seen_events = []
        self.render_calls = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def renderer(self, containers, container_configs, template_dir, adapters):
        """Intercept and record all calls to render config files."""
        self.render_calls.append(
            (containers, container_configs, template_dir, adapters)
        )

    def configure_charm(self, event):
        """Intercept and record full charm configuration events."""
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        return "cinder.juju"


class TestCinderOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Cinder Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequiresEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass

        self.harness = test_utils.get_harness(
            _CinderOperatorCharm, container_calls=self.container_calls
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("cinder-api")
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_cinder_conf_contains_region_name(self):
        """Test cinder.conf includes region_name from config."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)

        container = self.harness.charm.unit.get_container("cinder-api")
        client = container._pebble  # type: ignore
        with client.pull("/etc/cinder/cinder.conf") as infile:
            received_data = infile.read()

        # Default region in charmcraft.yaml is RegionOne
        self.assertIn("region_name = RegionOne", received_data)
