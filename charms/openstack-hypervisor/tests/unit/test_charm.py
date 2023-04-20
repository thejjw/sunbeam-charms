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

"""Tests for Openstack hypervisor charm."""

import base64
import json

import ops_sunbeam.test_utils as test_utils

import charm


class _HypervisorOperatorCharm(charm.HypervisorOperatorCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    PATCHES = ["subprocess", "socket"]

    def setUp(self):
        """Setup OpenStack Hypervisor tests."""
        super().setUp(charm, self.PATCHES)
        with open("config.yaml", "r") as f:
            config_data = f.read()
        self.harness = test_utils.get_harness(
            _HypervisorOperatorCharm,
            container_calls=self.container_calls,
            charm_config=config_data,
        )
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        rel_id = self.harness.add_relation("certificates", "vault")
        self.harness.add_relation_unit(rel_id, "vault/0")
        self.harness.update_config({"snap-channel": "essex/stable", "ip-address": "10.0.0.10"})
        self.harness.begin_with_initial_hooks()
        csr = {"certificate_signing_request": test_utils.TEST_CSR}
        self.harness.update_relation_data(
            rel_id,
            self.harness.charm.unit.name,
            {
                "ingress-address": "10.0.0.34",
                "certificate_signing_requests": json.dumps([csr]),
            },
        )
        test_utils.add_certificates_relation_certs(self.harness, rel_id)
        ovs_rel_id = self.harness.add_relation("ovsdb-cms", "ovn-relay")
        self.harness.add_relation_unit(ovs_rel_id, "ovn-relay/0")
        self.harness.update_relation_data(
            ovs_rel_id,
            "ovn-relay/0",
            {
                "bound-address": "10.1.176.143",
                "bound-hostname": "ovn-relay-0.ovn-relay-endpoints.openstack.svc.cluster.local",
                "egress-subnets": "10.20.21.10/32",
                "ingress-address": "10.20.21.10",
                "ingress-bound-address": "10.20.21.10",
                "private-address": "10.20.21.10",
            },
        )

    def test_all_relations(self):
        """Test all the charms relations."""
        self.socket.getfqdn.return_value = "test.local"
        self.initial_setup()
        self.harness.set_leader()
        self.subprocess.check_call.assert_any_call(
            ["snap", "install", "openstack-hypervisor", "--channel", "essex/stable"]
        )
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        metadata = self.harness.charm.metadata_secret()
        ovn_cacert = test_utils.TEST_CA + "\n" + "\n".join(test_utils.TEST_CHAIN)
        ovn_cacert = base64.b64encode(ovn_cacert.encode()).decode()
        private_key = base64.b64encode(
            self.harness.charm.contexts().certificates.key.encode()
        ).decode()
        certificate = base64.b64encode(test_utils.TEST_SERVER_CERT.encode()).decode()
        expect_settings = [
            "compute.cpu-mode=host-model",
            "compute.spice-proxy-address=10.0.0.10",
            "compute.virt-type=kvm",
            f"credentials.ovn-metadata-proxy-shared-secret={metadata}",
            "identity.auth-url=None",
            "identity.password=user-password",
            "identity.project-domain-name=pdomain_-ame",
            "identity.project-name=user-project",
            "identity.region-name=region12",
            "identity.user-domain-name=udomain-name",
            "identity.username=username",
            "logging.debug=false",
            "network.dns-domain=openstack.local",
            "network.dns-servers=8.8.8.8",
            "network.enable-gateway=false",
            "network.external-bridge=br-ex",
            "network.external-bridge-address=10.20.20.1/24",
            "network.ip-address=10.0.0.10",
            f"network.ovn-cacert={ovn_cacert}",
            f"network.ovn-cert={certificate}",
            f"network.ovn-key={private_key}",
            "network.ovn-sb-connection=ssl:10.20.21.10:6642",
            "network.physnet-name=physnet1",
            "node.fqdn=test.local",
            "node.ip-address=10.0.0.10",
            "rabbitmq.url=rabbit://hypervisor:rabbit.pass@10.0.0.13:5672/openstack",
        ]
        expect_set_cmd = ["snap", "set", "openstack-hypervisor"]
        expect_set_cmd.extend(expect_settings)
        self.subprocess.check_call.assert_any_call(expect_set_cmd)
