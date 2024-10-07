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

"""Tests for masakari-k8s charm."""

import charm
import ops_sunbeam.test_utils as test_utils


class _MasakariOperatorCharm(charm.MasakariOperatorCharm):
    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        return "masakari.juju"


class TestMasakariOperatorCharm(test_utils.CharmTestCase):
    """Class for testing masakari-k8s charm."""

    PATCHES = []

    def setUp(self):
        """Run setup for unit tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _MasakariOperatorCharm,
            container_calls=self.container_calls,
        )

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

        self.addCleanup(self.harness.cleanup)

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.harness.begin()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(
            self.harness.charm.seen_events,
            ["PebbleReadyEvent", "PebbleReadyEvent"],
        )

    def test_all_relations(self):
        """Test all the charm's relations for Masakari."""
        self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)

        self.harness.charm.configure_charm(event=None)

        setup_cmds = [
            [
                "masakari-manage",
                "db",
                "sync",
            ],
        ]

        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["masakari-api"])

        # Check for rendering of both configuration files in masakari-api
        for f in [
            "/etc/apache2/sites-available/wsgi-masakari-api.conf",
            "/etc/masakari/masakari.conf",
        ]:
            self.check_file("masakari-api", f)

        # Check for rendering of single configuration file in masakari-engine
        self.check_file("masakari-engine", "/etc/masakari/masakari.conf")
