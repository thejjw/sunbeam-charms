#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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

"""Tests for openstack images sync charm."""
import pathlib

import charm
import ops_sunbeam.test_utils as test_utils
import yaml

class _OISOperatorCharm(charm.OpenstackImagesSyncK8SCharm):
    """Openstack Images Sync test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        """Log events."""
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        """Log configure charm call."""
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        """Ingress address for charm."""
        return "openstack-images-sync.juju"


class TestOISOperatorCharm(test_utils.CharmTestCase):
    """Classes for testing openstack images sync charms."""

    PATCHES = []
    maxDiff = None

    def setUp(self):
        """Setup openstack images sync tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _OISOperatorCharm,
            container_calls=self.container_calls,
        )

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        id_svc_rel = self.harness.model.get_relation("identity-service")
        self.harness.update_relation_data(
            id_svc_rel.id, "keystone", {"service-domain-id": "svcdomid"}
        )
        test_utils.add_complete_ingress_relation(self.harness)

        setup_cmds = [
            ["a2dissite", "000-default"],
        ]
        for cmd in setup_cmds:
            self.assertIn(
                cmd,
                self.container_calls.execute[self.harness.charm.service_name],
            )
        self.check_file(
            self.harness.charm.service_name,
            "/etc/apache2/sites-enabled/http-sync.conf",
        )
        expect_entries = """cloud_name: microstack
frequency: 3600
mirrors:
- content_id: '%(region)s' # Content ID choice
  custom_properties: {}
  hypervisor_mapping: false
  image_conversion: false
  item_filters:
  - release~(focal|jammy|noble)
  - arch~(amd64)
  - ftype~(disk1.img|disk.img)
  keep_items: false
  latest_property: false
  max_items: 1
  url: http://cloud-images.ubuntu.com/releases
  path: streams/v1/index.sjson
  regions: [RegionOne]
  visibility: public
name_prefix: auto-sync/
output_directory: /var/www/html/simplestreams"""
        self.check_file(
            self.harness.charm.service_name,
            "/etc/openstack-images-sync/config.yaml",
            contents=expect_entries,
        )
