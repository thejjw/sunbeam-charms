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

"""Tests for glance charm."""

import charm
import ops_sunbeam.test_utils as test_utils
from mock import (
    patch,
)


class _GlanceOperatorCharm(charm.GlanceOperatorCharm):
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
        return "glance.juju"


class TestGlanceOperatorCharm(test_utils.CharmTestCase):
    """Class for testing glance charm."""

    PATCHES = []

    def setUp(self):
        """Setup Glance tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _GlanceOperatorCharm, container_calls=self.container_calls
        )
        mock_get_platform = patch(
            "charmhelpers.osplatform.get_platform", return_value="ubuntu"
        )
        mock_get_platform.start()

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.database_requires import (
            DatabaseEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(mock_get_platform.stop)
        self.addCleanup(self.harness.cleanup)
        test_utils.add_complete_ingress_relation(self.harness)

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.harness.begin()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all the charms relations."""
        ceph_rel_id = self.harness.add_relation("ceph", "ceph-mon")
        self.harness.begin_with_initial_hooks()
        self.harness.add_relation_unit(ceph_rel_id, "ceph-mon/0")
        self.harness.update_relation_data(
            ceph_rel_id, "ceph-mon/0", {"ingress-address": "10.0.0.33"}
        )
        test_utils.add_ceph_relation_credentials(self.harness, ceph_rel_id)
        test_utils.add_api_relations(self.harness)
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        ceph_install_cmds = [
            [
                "ceph-authtool",
                "/etc/ceph/ceph.client.glance-k8s.keyring",
                "--create-keyring",
                "--name=client.glance-k8s",
                "--add-key=AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==",
            ],
        ]
        for cmd in ceph_install_cmds:
            self.assertIn(cmd, self.container_calls.execute["glance-api"])

        app_setup_cmds = [
            ["a2enmod", "proxy_http"],
            [
                "sudo",
                "-u",
                "glance",
                "glance-manage",
                "--config-dir",
                "/etc/glance",
                "db",
                "sync",
            ],
        ]
        for cmd in app_setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["glance-api"])

        for f in [
            "/etc/apache2/sites-enabled/glance-forwarding.conf",
            "/etc/glance/glance-api.conf",
            "/etc/ceph/ceph.conf",
        ]:
            self.check_file("glance-api", f)
