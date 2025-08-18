#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
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

"""Tests for OpenStack Manila Data Operator charm."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops_sunbeam.test_utils as test_utils
from ops import (
    model,
)


class _ManilaDataOperatorCharm(charm.ManilaDataOperatorCharm):
    """manila-data test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    """Test charm to test relations."""

    PATCHES = []

    def setUp(self):
        """Setup OpenStack Manila Data Operator tests."""
        super().setUp(charm, self.PATCHES)
        self.snap = Mock()
        snap_patch = patch.object(
            _ManilaDataOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _ManilaDataOperatorCharm,
            container_calls=self.container_calls,
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
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Setting up relations."""
        self.harness.update_config({"snap-channel": "essex/stable"})
        self.harness.begin_with_initial_hooks()

    def all_required_relations_setup(self):
        """Setting up all the required relations."""
        self.initial_setup()
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        test_utils.add_complete_db_relation(self.harness)

    def test_mandatory_relations(self):
        """Test all the charms relations."""
        manila_data_snap_mock = MagicMock()
        manila_data_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        self.snap.SnapCache.return_value = {
            "manila-data": manila_data_snap_mock
        }
        self.initial_setup()
        self.harness.set_leader()

        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        test_utils.add_complete_db_relation(self.harness)

        expect_settings = {
            "rabbitmq.url": "rabbit://manila-data:rabbit.pass@rabbithost1.local:5672/openstack",
            "database.url": "mysql+pymysql://foo:hardpassword@10.0.0.10/manila",
            "settings.debug": False,
            "settings.enable-telemetry-notifications": False,
        }
        manila_data_snap_mock.set.assert_any_call(expect_settings, typed=True)
        self.assertIsInstance(
            self.harness.charm.status.status, model.ActiveStatus
        )

    def test_all_relations(self):
        """Test all the charms relations."""
        manila_data_snap_mock = MagicMock()
        manila_data_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        self.snap.SnapCache.return_value = {
            "manila-data": manila_data_snap_mock
        }
        self.all_required_relations_setup()

        self.assertEqual(self.harness.charm.status.message(), "")
        self.assertEqual(self.harness.charm.status.status.name, "active")
