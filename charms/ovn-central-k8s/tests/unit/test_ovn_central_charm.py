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

import mock

import charm
import ops_sunbeam.test_utils as test_utils


class _OVNCentralXenaOperatorCharm(charm.OVNCentralXenaOperatorCharm):

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    def configure_ovn_listener(self, db, port_map):
        pass

    def cluster_status(self, db, cmd_executor):
        if db == 'ovnnb_db':
            nb_mock = mock.MagicMock()
            nb_mock.cluster_id = 'nb_id'
            return nb_mock
        if db == 'ovnsb_db':
            sb_mock = mock.MagicMock()
            sb_mock.cluster_id = 'sb_id'
            return sb_mock


class TestOVNCentralXenaOperatorCharm(test_utils.CharmTestCase):

    PATCHES = [
        'KubernetesServicePatch',
    ]

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _OVNCentralXenaOperatorCharm,
            container_calls=self.container_calls)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 3)

    def check_rendered_files(self):
        sb_config_files = [
            '/etc/ovn/cert_host',
            '/etc/ovn/key_host',
            '/etc/ovn/ovn-central.crt',
            '/root/ovn-sb-cluster-join.sh',
            '/root/ovn-sb-db-server-wrapper.sh']
        for f in sb_config_files:
            self.check_file('ovn-sb-db-server', f)

        nb_config_files = [
            '/etc/ovn/cert_host',
            '/etc/ovn/key_host',
            '/etc/ovn/ovn-central.crt',
            '/root/ovn-nb-cluster-join.sh',
            '/root/ovn-nb-db-server-wrapper.sh']
        for f in nb_config_files:
            self.check_file('ovn-nb-db-server', f)

        northd_config_files = [
            '/etc/ovn/cert_host',
            '/etc/ovn/key_host',
            '/etc/ovn/ovn-central.crt',
            '/etc/ovn/ovn-northd-db-params.conf',
            '/root/ovn-northd-wrapper.sh']
        for f in northd_config_files:
            self.check_file('ovn-northd', f)

    def test_all_relations_leader(self):
        self.harness.set_leader()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        self.check_rendered_files()

    def test_all_relations_non_leader(self):
        self.harness.set_leader(False)
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        rel_ids = test_utils.add_all_relations(self.harness)
        test_utils.set_remote_leader_ready(
            self.harness,
            rel_ids['peers'])
        self.harness.update_relation_data(
            rel_ids['peers'],
            self.harness.charm.app.name,
            {
                'nb_cid': 'nbcid',
                'sb_cid': 'sbcid'}
        )
        self.check_rendered_files()
        self.assertEqual(
            self.container_calls.execute['ovn-sb-db-server'],
            [
                ['bash', '/root/ovn-sb-cluster-join.sh']
            ])
        self.assertEqual(
            self.container_calls.execute['ovn-nb-db-server'],
            [
                ['bash', '/root/ovn-nb-cluster-join.sh']
            ])
