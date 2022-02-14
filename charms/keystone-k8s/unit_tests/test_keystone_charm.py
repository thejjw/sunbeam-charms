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
import json
import sys

sys.path.append('lib')  # noqa
sys.path.append('src')  # noqa

import charm
import advanced_sunbeam_openstack.test_utils as test_utils


class _KeystoneWallabyOperatorCharm(charm.KeystoneWallabyOperatorCharm):

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    def public_ingress_address(self) -> str:
        return '10.0.0.10'


class TestKeystoneOperatorCharm(test_utils.CharmTestCase):

    PATCHES = [
        'manager'
    ]

    def add_id_relation(self) -> str:
        """Add amqp relation."""
        rel_id = self.harness.add_relation("identity-service", "cinder")
        self.harness.add_relation_unit(rel_id, "cinder/0")
        self.harness.update_relation_data(
            rel_id, "cinder/0", {"ingress-address": "10.0.0.13"}
        )
        interal_url = "http://10.152.183.228:8776"
        public_url = "http://10.152.183.228:8776"
        self.harness.update_relation_data(
            rel_id,
            "cinder",
            {
                "region": "RegionOne",
                "service-endpoints": json.dumps(
                    [
                        {
                            "service_name": "cinderv2",
                            "type": "volumev2",
                            "description": "Cinder Volume Service v2",
                            "internal_url": f"{interal_url}/v2/$(tenant_id)s",
                            "public_url": f"{public_url}/v2/$(tenant_id)s",
                            "admin_url": f"{interal_url}/v2/$(tenant_id)s"},
                        {
                            "service_name": "cinderv3",
                            "type": "volumev3",
                            "description": "Cinder Volume Service v3",
                            "internal_url": f"{interal_url}/v3/$(tenant_id)s",
                            "public_url": f"{public_url}/v3/$(tenant_id)s",
                            "admin_url": f"{interal_url}/v3/$(tenant_id)s"}])})
        return rel_id

    def ks_manager_mock(self):
        def _create_mock(p_name, p_id):
            _mock = mock.MagicMock()
            type(_mock).name = mock.PropertyMock(
                return_value=p_name)
            type(_mock).id = mock.PropertyMock(
                return_value=p_id)
            return _mock

        service_domain_mock = _create_mock('sdomain_name', 'sdomain_id')
        admin_domain_mock = _create_mock('adomain_name', 'adomain_id')

        admin_project_mock = _create_mock('aproject_name', 'aproject_id')

        service_user_mock = _create_mock('suser_name', 'suser_id')
        admin_user_mock = _create_mock('auser_name', 'auser_id')

        admin_role_mock = _create_mock('arole_name', 'arole_id')

        km_mock = mock.MagicMock()
        km_mock.get_domain.return_value = admin_domain_mock
        km_mock.get_project.return_value = admin_project_mock
        km_mock.get_user.return_value = admin_user_mock
        km_mock.create_domain.return_value = service_domain_mock
        km_mock.create_user.return_value = service_user_mock
        km_mock.create_role.return_value = admin_role_mock
        return km_mock

    @mock.patch(
        'charms.observability_libs.v0.kubernetes_service_patch.'
        'KubernetesServicePatch')
    def setUp(self, mock_svc_patch):
        super().setUp(charm, self.PATCHES)
        self.km_mock = self.ks_manager_mock()
        self.manager.KeystoneManager.return_value = self.km_mock
        self.harness = test_utils.get_harness(
            _KeystoneWallabyOperatorCharm,
            container_calls=self.container_calls)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready('keystone')
        self.assertEqual(self.harness.charm.seen_events, ['PebbleReadyEvent'])

    def test_id_client(self):
        self.harness.set_leader()
        self.harness.add_relation('peers', 'keystone')
        self.harness.container_pebble_ready('keystone')
        test_utils.add_db_relation_credentials(
            self.harness,
            test_utils.add_base_db_relation(self.harness))
        identity_rel_id = self.add_id_relation()
        rel_data = self.harness.get_relation_data(
            identity_rel_id,
            self.harness.charm.unit.app.name)
        self.assertEqual(
            rel_data,
            {
                'admin-domain-id': 'adomain_id',
                'admin-domain-name': 'adomain_name',
                'admin-project-id': 'aproject_id',
                'admin-project-name': 'aproject_name',
                'admin-user-id': 'auser_id',
                'admin-user-name': 'auser_name',
                'api-version': 'v3',
                'auth-host': '10.0.0.10',
                'auth-port': '5000',
                'auth-protocol': 'http',
                'internal-host': '10.0.0.10',
                'internal-port': '5000',
                'internal-protocol': 'http',
                'service-domain-id': 'sdomain_id',
                'service-domain-name': 'sdomain_name',
                'service-host': '10.0.0.10',
                'service-password': 'password123',
                'service-port': '5000',
                'service-project-id': 'aproject_id',
                'service-project-name': 'aproject_name',
                'service-protocol': 'http',
                'service-user-id': 'suser_id',
                'service-user-name': 'suser_name'})

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
        self.km_mock.setup_keystone.assert_called_once_with()
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
