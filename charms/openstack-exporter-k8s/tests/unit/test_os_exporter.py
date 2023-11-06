#!/usr/bin/env python3

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

"""Unit tests for Openstack Exporter operator."""

import json

import ops_sunbeam.test_utils as test_utils
from mock import (
    Mock,
)
from ops.testing import (
    Harness,
)

import charm


class _OSExporterTestOperatorCharm(charm.OSExporterOperatorCharm):
    """Test Operator Charm for Openstack Exporter Operator."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)


class TestOSExporterOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for OSExporter Operator."""

    PATCHES = []

    def setUp(self):
        """Set up environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _OSExporterTestOperatorCharm, container_calls=self.container_calls
        )

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def add_complete_identity_resource_relation(self, harness: Harness) -> int:
        """Add complete Identity resource relation."""
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.user_id_ops.get_config_credentials = Mock(
            return_value=("test", "test")
        )
        harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "response": json.dumps(
                    {
                        "id": 1,
                        "tag": "create_user",
                        "ops": [{"name": "create_user", "return-code": 0}],
                    }
                )
            },
        )
        return rel_id

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 1)

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        self.add_complete_identity_resource_relation(self.harness)

        config_files = [
            "/etc/os-exporter/clouds.yaml",
        ]
        for f in config_files:
            self.check_file("openstack-exporter", f)
