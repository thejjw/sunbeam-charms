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

"""Tests for watcher charm."""

from pathlib import (
    Path,
)

import charm
import ops_sunbeam.test_utils as test_utils
import yaml

charmcraft = (Path(__file__).parents[2] / "charmcraft.yaml").read_text()
config = yaml.dump(yaml.safe_load(charmcraft)["config"])
actions = yaml.dump(yaml.safe_load(charmcraft)["actions"])


class _WatcherOperatorCharm(charm.WatcherOperatorCharm):
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
        return "watcher.juju"


class TestWatcherOperatorCharm(test_utils.CharmTestCase):
    """Class for testing watcher charm."""

    PATCHES = []

    def setUp(self):
        """Setup Watcher tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _WatcherOperatorCharm,
            container_calls=self.container_calls,
            charm_metadata=charmcraft,
            charm_config=config,
            charm_actions=actions,
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
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
            ["PebbleReadyEvent", "PebbleReadyEvent", "PebbleReadyEvent"],
        )

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)
        # Add gnochi-db optional relation
        self.harness.add_relation(
            "gnocchi-db", "gnocchi", app_data={"ready": "true"}
        )

        setup_cmds = [
            [
                "sudo",
                "-u",
                "watcher",
                "watcher-db-manage",
                "upgrade",
            ],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["watcher-api"])

        for f in [
            "/etc/apache2/sites-available/wsgi-watcher-api.conf",
            "/etc/watcher/watcher.conf",
        ]:
            self.check_file("watcher-api", f)

        self.check_file("watcher-decision-engine", "/etc/watcher/watcher.conf")
        self.check_file("watcher-applier", "/etc/watcher/watcher.conf")
