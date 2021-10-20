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

import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

from ops.testing import Harness

import charm
import mock
import advanced_sunbeam_openstack.test_utils as test_utils


class _KeystoneVictoriaOperatorCharm(charm.KeystoneVictoriaOperatorCharm):

    def __init__(self, framework):
        self.seen_events = []
        self.render_calls = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def renderer(self, containers, container_configs, template_dir,
                 openstack_release, adapters):
        self.render_calls.append(
            (
                containers,
                container_configs,
                template_dir,
                openstack_release,
                adapters))

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)


class TestKeystoneOperatorCharm(test_utils.CharmTestCase):

    PATCHES = [
        'KubernetesServicePatch',
        'manager'
    ]

    def setUp(self):
        self.container_calls = {
            'push': {},
            'pull': [],
            'remove_path': []}

        super().setUp(charm, self.PATCHES)
        self.km_mock = mock.MagicMock()
        self.manager.KeystoneManager.return_value = self.km_mock
        self.harness = test_utils.get_harness(
            _KeystoneVictoriaOperatorCharm,
            container_calls=self.container_calls)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()


    def test_pebble_ready_handler(self):
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready('keystone')
        self.assertEqual(self.harness.charm.seen_events, ['PebbleReadyEvent'])

    def test_leader_bootstraps(self):
        self.harness.set_leader()
        rel_id = self.harness.add_relation('peers', 'keystone')
        self.harness.add_relation_unit(
           rel_id,
            'keystone/1')
        self.harness.container_pebble_ready('keystone')
        test_utils.add_db_relation_credentials(
            self.harness,
            test_utils.add_base_db_relation(self.harness))
        container = self.harness.charm.unit.get_container(
            self.harness.charm.wsgi_container_name)
        self.km_mock.setup_keystone.assert_called_once_with(
            container) 
        self.km_mock.setup_initial_projects_and_users.assert_called_once_with() 

    def test_non_leader_no_bootstraps(self):
        self.harness.set_leader(False)
        rel_id = self.harness.add_relation('peers', 'keystone')
        self.harness.add_relation_unit(
            rel_id,
            'keystone/1')
        self.harness.container_pebble_ready('keystone')
        test_utils.add_db_relation_credentials(
            self.harness,
            test_utils.add_base_db_relation(self.harness))
        self.assertFalse(
            self.km_mock.setup_keystone.called)
