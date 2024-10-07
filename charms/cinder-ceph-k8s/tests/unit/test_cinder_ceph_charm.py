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

"""Unit tests for Cinder Ceph operator charm class."""

import json
from unittest.mock import (
    MagicMock,
    patch,
)

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)


class _CinderCephOperatorCharm(charm.CinderCephOperatorCharm):
    """Charm wrapper for test usage."""

    openstack_release = "wallaby"

    def __init__(self, framework):
        self.seen_events = []
        self.render_calls = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def renderer(
        self,
        containers,
        container_configs,
        template_dir,
        openstack_release,
        adapters,
    ):
        self.render_calls.append(
            (
                containers,
                container_configs,
                template_dir,
                openstack_release,
                adapters,
            )
        )

    def _on_service_pebble_ready(self, event):
        super()._on_service_pebble_ready(event)
        self._log_event(event)


def add_complete_storage_backend_relation(harness: Harness) -> None:
    """Add complete storage-backend relation."""
    storage_backend_rel = harness.add_relation("storage-backend", "cinder")
    harness.add_relation_unit(storage_backend_rel, "cinder/0")
    harness.update_relation_data(
        storage_backend_rel, "cinder", {"ready": json.dumps("true")}
    )


class TestCinderCephOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderCephOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.harness = test_utils.get_harness(
            _CinderCephOperatorCharm,
            container_calls=self.container_calls,
        )
        mock_get_platform = patch(
            "charmhelpers.osplatform.get_platform", return_value="ubuntu"
        )
        mock_get_platform.start()

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

        self.addCleanup(mock_get_platform.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        test_utils.add_complete_ceph_relation(self.harness)
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_db_relation(self.harness)
        add_complete_storage_backend_relation(self.harness)
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )
        # self.assertTrue(self.harness.charm.relation_handlers_ready())

    def test_ceph_access(self):
        """Test charm provides secret via ceph-access."""
        self.harness.begin_with_initial_hooks()
        self.harness.set_leader()
        test_utils.add_complete_ceph_relation(self.harness)
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_db_relation(self.harness)
        access_rel = self.harness.add_relation(
            "ceph-access", "openstack-hypervisor"
        )
        add_complete_storage_backend_relation(self.harness)
        test_utils.set_all_pebbles_ready(self.harness)
        # self.assertTrue(self.harness.charm.relation_handlers_ready())
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )
        rel_data = self.harness.get_relation_data(
            access_rel, self.harness.charm.unit.app.name
        )
        self.assertRegex(rel_data["access-credentials"], "^secret:.*")
