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

"""Unit tests for the Manila Share (Cephfs) K8s Operator charm."""

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)


class _ManilaCephfsCharm(charm.ManilaShareCephfsCharm):
    """Test implementation of Manila Share (Cephfs) Operator charm."""

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


class TestManilaCephfsCharm(test_utils.CharmTestCase):
    """Unit tests for Manila Share (Cephfs) Operator charm."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _ManilaCephfsCharm, container_calls=self.container_calls
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def add_ceph_nfs_client_relation(self, harness: Harness) -> None:
        """Add the ceph-nfs relation and unit data."""
        app_data = {
            "client": "client.foo",
            "cluster-id": "lish",
            "fsid": "fake-fsid",
            "keyring": "keys-do-not-ring",
            "mon-hosts": '["mony"]',
            "volume": "voly",
        }
        return harness.add_relation("ceph-nfs", "microceph", app_data=app_data)

    def _check_file_contents(self, container, path, strings=None, excluded_strings=None):
        strings = strings or []
        excluded_strings = excluded_strings or []
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore

        with client.pull(path) as infile:
            received_data = infile.read()

        for string in strings:
            self.assertIn(string, received_data)

        # We may expect the file to not contain certain strings.
        for string in excluded_strings:
            self.assertNotIn(string, received_data)

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("manila-share")
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)

        # The files should not contain ceph-related options set if the relation
        # is not present.
        self._check_file_contents(
            "manila-share",
            "/etc/ceph/ceph.conf",
            excluded_strings=["keyring"],
        )
        self._check_file_contents(
            "manila-share",
            "/etc/ceph/manila.keyring",
            excluded_strings=["key = "],
        )
        self._check_file_contents(
            "manila-share",
            "/etc/manila/manila.conf",
            excluded_strings=["enabled_share_backends", "share_backend_name"],
        )

        ceph_rel_id = self.add_ceph_nfs_client_relation(self.harness)

        # Now that the relation is added, we should have the ceph-related
        # options set.
        ceph_conf_strings = ["[foo]", "mon host = mony"]
        self._check_file_contents(
            "manila-share",
            "/etc/ceph/ceph.conf",
            ceph_conf_strings,
        )
        keyring_strings = ["[foo]", "key = keys-do-not-ring"]
        self._check_file_contents(
            "manila-share",
            "/etc/ceph/manila.keyring",
            keyring_strings,
        )
        manila_strings = [
            "enabled_share_backends = cephfsnative1",
            "[cephfsnative1]",
            "share_backend_name = CEPHFSNATIVE1",
            "cephfs_auth_id = foo",
            "cephfs_cluster_name = lish",
            "cephfs_filesystem_name = voly",
        ]
        self._check_file_contents(
            "manila-share",
            "/etc/manila/manila.conf",
            manila_strings,
        )

        # Remove the ceph-nfs relation, the config files should no longer have
        # the ceph-related options from above.
        self.harness.remove_relation(ceph_rel_id)

        self._check_file_contents(
            "manila-share",
            "/etc/ceph/ceph.conf",
            excluded_strings=ceph_conf_strings,
        )
        self._check_file_contents(
            "manila-share",
            "/etc/ceph/manila.keyring",
            excluded_strings=keyring_strings,
        )
        self._check_file_contents(
            "manila-share",
            "/etc/manila/manila.conf",
            excluded_strings=manila_strings,
        )
