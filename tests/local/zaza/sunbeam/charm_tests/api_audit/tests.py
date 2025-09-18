# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
import re
import subprocess

import barbicanclient.client as barbican_client
import zaza.openstack.charm_tests.test_utils as test_utils
from zaza.openstack.utilities import openstack as openstack_utils


class OpenStackAPIAuditTest(test_utils.BaseCharmTest):
    """Charm tests for API audit logging."""

    application_name = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass(application_name=cls.application_name)

        cls.keystone_session = openstack_utils.get_overcloud_keystone_session()

        auth = openstack_utils.get_overcloud_auth()
        cls.keystone_client = openstack_utils.get_keystone_client(auth)

    @classmethod
    def get_pod_logs(cls, pod_name, since="5m",
                     container=None, all_containers=True):
        # We expect the k8s namespace name to match the model name.
        namespace = cls.model_name

        cmd = ["sudo", "k8s", "kubectl", "logs", "-n", namespace,
               f"pod/{pod_name}"]
        if all_containers:
            cmd += ["--all-containers"]
        if container:
            cmd += ["--container", container]

        result = subprocess.run(cmd, check=True, capture_output=True)
        return result.stdout.decode()

    def _trigger_audit_event(self):
        # Perform any API action that is expected to trigger an audit event.
        pass

    def check_audit_logs(self, exp_msg):
        self._trigger_audit_event()

        # For simplicity we expect there to be just one pod.
        # If needed, we can check multiple pods or aggregate the
        # logs using COS.
        pod_name = f"{self.application_name}-0"
        pod_logs = self.get_pod_logs(pod_name)

        assert exp_msg in pod_logs, (
            f"{pod_name} logs do not contain the expected message: {exp_msg}")

        # We'll ensure that all services use the same log format, making those
        # records easier to parse.
        #
        # {timestamp} {pid} {log level} oslo.messaging.notification.*
        #     [{global-req-id} {req-id} {user context}] {payload}
        audit_re = (
            r"\d+-\d+-\d+ \d+:\d+:\d+\.\d+ \d+ "
            r"INFO oslo.messaging.notification[\w.]+ \[.* "
            r"req-[\w-]+ .*\] \{.*\}")
        assert re.search(audit_re, pod_logs), (
            f"{pod_name} logs do not follow the expected format")


class KeystoneAPIAuditTest(OpenStackAPIAuditTest):
    application_name = "keystone"

    def test_audit(self):
        name = f"test-%s" % random.randint(0, 32768)

        # Create a domain to trigger an event.
        self.keystone_client.domains.create(name, enabled=False)
        exp_msg = (
            "oslo.messaging.notification.identity.domain.created")
        self.check_audit_logs(exp_msg)


class AuditMiddlewareTest(OpenStackAPIAuditTest):
    """Base class for services that use the audit paste middleware"""

    def test_audit(self):
        exp_msg = "oslo.messaging.notification.audit.http.request"
        self.check_audit_logs(exp_msg)


class AodhAPIAuditTest(AuditMiddlewareTest):
    application_name = "aodh"

    def _trigger_audit_event(self):
        client = openstack_utils.get_aodh_session_client(
            self.keystone_session)
        client.alarm.list()


class BarbicanAPIAuditTest(AuditMiddlewareTest):
    application_name = "barbican"

    def _trigger_audit_event(self):
        barbican_endpoint = self.keystone_client.service_catalog.url_for(
            service_type='key-manager', interface='publicURL')
        client = barbican_client.Client(session=self.keystone_session,
                                        endpoint=barbican_endpoint)
        client.secrets.list()


class CinderAPIAuditTest(AuditMiddlewareTest):
    application_name = "cinder"

    def _trigger_audit_event(self):
        client = openstack_utils.get_cinder_session_client(
            self.keystone_session)
        client.volumes.list()


class DesignateAPIAuditTest(AuditMiddlewareTest):
    application_name = "designate"

    def _trigger_audit_event(self):
        client = openstack_utils.get_designate_session_client(
            session=self.keystone_session)
        client.zones.list()


class GlanceAPIAuditTest(AuditMiddlewareTest):
    application_name = "glance"

    def _trigger_audit_event(self):
        client = openstack_utils.get_glance_session_client(
            self.keystone_session)
        client.images.list()


class HeatAPIAuditTest(AuditMiddlewareTest):
    application_name = "heat"

    def _trigger_audit_event(self):
        client = openstack_utils.get_heat_session_client(
            self.keystone_session)
        client.stacks.list()


class MagnumAPIAuditTest(AuditMiddlewareTest):
    application_name = "magnum"

    def _trigger_audit_event(self):
        client = openstack_utils.get_magnum_session_client(
            self.keystone_session)
        client.clusters.list()


class MasakariAPIAuditTest(AuditMiddlewareTest):
    application_name = "masakari"

    def _trigger_audit_event(self):
        client = openstack_utils.get_masakari_session_client(
            self.keystone_session)
        for segment in client.segments():
            pass


class NovaAPIAuditTest(AuditMiddlewareTest):
    application_name = "nova"

    def _trigger_audit_event(self):
        client = openstack_utils.get_nova_session_client(
            self.keystone_session)
        client.servers.list()


class NeutronAPIAuditTest(AuditMiddlewareTest):
    application_name = "neutron"

    def _trigger_audit_event(self):
        client = openstack_utils.get_neutron_session_client(
            self.keystone_session)
        client.list_networks()


class OctaviaAPIAuditTest(AuditMiddlewareTest):
    application_name = "octavia"

    def _trigger_audit_event(self):
        client = openstack_utils.get_octavia_session_client(
            self.keystone_session)
        client.amphora_list()

class CloudkittyAPIAuditTest(AuditMiddlewareTest):
    application_name = "cloudkitty"

    def _trigger_audit_event(self):
        client = openstack_utils.get_cloudkitty_session_client(
            self.keystone_session)
        client.module_list()