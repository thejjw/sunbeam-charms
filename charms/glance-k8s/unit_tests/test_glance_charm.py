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
import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

import charm
import advanced_sunbeam_openstack.test_utils as test_utils


class _GlanceXenaOperatorCharm(charm.GlanceXenaOperatorCharm):

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

    PATCHES = []

    def setUp(self):
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _GlanceXenaOperatorCharm,
            container_calls=self.container_calls)
        self.addCleanup(self.harness.cleanup)
        test_utils.add_complete_ingress_relation(self.harness)

    @mock.patch(
        'charms.observability_libs.v0.kubernetes_service_patch.'
        'KubernetesServicePatch')
    def test_pebble_ready_handler(self, svc_patch):
        self.harness.begin()
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ['PebbleReadyEvent'])

    @mock.patch(
        'charms.observability_libs.v0.kubernetes_service_patch.'
        'KubernetesServicePatch')
    def test_all_relations(self, svc_patch):
        ceph_rel_id = self.harness.add_relation("ceph", "ceph-mon")
        self.harness.begin_with_initial_hooks()
        self.harness.add_relation_unit(ceph_rel_id, "ceph-mon/0")
        self.harness.update_relation_data(
            ceph_rel_id,
            "ceph-mon/0",
            {"ingress-address": "10.0.0.33"})
        test_utils.add_ceph_relation_credentials(self.harness, ceph_rel_id)
        test_utils.add_api_relations(self.harness)
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        ceph_install_cmds = [
            ['apt', 'update'],
            ['apt', 'install', '-y', 'ceph-common'],
            ['ceph-authtool',
             '/etc/ceph/ceph.client.sunbeam-glance-operator.keyring',
             '--create-keyring',
             '--name=client.sunbeam-glance-operator',
             '--add-key=AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==']]
        for cmd in ceph_install_cmds:
            self.assertIn(cmd, self.container_calls.execute['glance-api'])

        app_setup_cmds = [
            ['a2ensite', 'wsgi-glance-api'],
            ['sudo', '-u', 'glance', 'glance-manage', '--config-dir',
             '/etc/glance', 'db', 'sync']]
        for cmd in app_setup_cmds:
            self.assertIn(cmd, self.container_calls.execute['glance-api'])

        for f in ['/etc/apache2/sites-available/wsgi-glance-api.conf',
                  '/etc/glance/glance-api.conf', '/etc/ceph/ceph.conf']:
            self.check_file('glance-api', f)
