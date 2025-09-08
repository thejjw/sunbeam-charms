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
from unittest.mock import (
    MagicMock,
)

import charm
import charms.operator_libs_linux.v2.snap as snap
import ops
import ops.testing
import ops_sunbeam.test_utils as test_utils
from ops_sunbeam import guard as sunbeam_guard


class _HypervisorOperatorCharm(charm.HypervisorOperatorCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    """Test charm to test relations."""

    PATCHES = [
        "socket",
        "snap",
        "get_local_ip_by_default_route",
        "os",
        "subprocess",
        "service_running",
    ]

    def setUp(self):
        """Setup OpenStack Hypervisor tests."""
        super().setUp(charm, self.PATCHES)
        self.service_running.return_value = False

        self.snap.SnapError = Exception
        self.harness = test_utils.get_harness(
            _HypervisorOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Setting up relations."""
        self.harness.update_config({"snap-channel": "essex/stable"})
        self.harness.begin_with_initial_hooks()
        test_utils.add_complete_certificates_relation(self.harness)
        self.harness.add_relation(
            "ovsdb-cms",
            "ovn-relay",
            app_data={"loadbalancer-address": "10.15.24.37"},
            unit_data={
                "bound-address": "10.1.176.143",
                "bound-hostname": "ovn-relay-0.ovn-relay-endpoints.openstack.svc.cluster.local",
                "egress-subnets": "10.20.21.10/32",
                "ingress-address": "10.20.21.10",
                "ingress-bound-address": "10.20.21.10",
                "private-address": "10.20.21.10",
            },
        )

        ceph_rel_id = self.harness.add_relation("ceph-access", "cinder-ceph")
        self.harness.add_relation_unit(ceph_rel_id, "cinder-ceph/0")

        credentials_content = {"uuid": "ddd", "key": "eee"}
        credentials_id = self.harness.add_model_secret(
            "cinder-ceph", credentials_content
        )

        self.harness.grant_secret(credentials_id, self.harness.charm.app.name)
        self.harness.update_relation_data(
            ceph_rel_id,
            "cinder-ceph",
            {"access-credentials": credentials_id},
        )

    def test_mandatory_relations(self):
        """Test all the charms relations."""
        self.get_local_ip_by_default_route.return_value = "10.0.0.10"
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        self.socket.getfqdn.return_value = "test.local"
        self.socket.gethostname.return_value = "test"
        self.initial_setup()
        self.harness.set_leader()

        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        # Add nova-service relation
        self.harness.add_relation(
            "nova-service",
            "nova",
            app_data={
                "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html"
            },
        )

        hypervisor_snap_mock.ensure.assert_any_call(
            "latest", channel="essex/stable"
        )
        metadata = self.harness.charm.metadata_secret()
        cacert = test_utils.TEST_CA
        cacert_with_intermediates = (
            test_utils.TEST_CA + "\n" + "\n".join(test_utils.TEST_CHAIN)
        )
        cacert = base64.b64encode(cacert.encode()).decode()
        cacert_with_intermediates = base64.b64encode(
            cacert_with_intermediates.encode()
        ).decode()
        private_key = base64.b64encode(
            self.harness.charm.contexts().certificates.key.encode()
        ).decode()
        certificate = base64.b64encode(
            test_utils.TEST_SERVER_CERT.encode()
        ).decode()
        expect_settings = {
            "compute.cpu-mode": "host-model",
            "compute.spice-proxy-address": "10.0.0.10",
            "compute.cacert": cacert,
            "compute.cert": certificate,
            "compute.key": private_key,
            "compute.migration-address": "10.0.0.10",
            "compute.pci-device-specs": None,
            "compute.resume-on-boot": True,
            "compute.rbd-user": "nova",
            "compute.rbd-secret-uuid": "ddd",
            "compute.rbd-key": "eee",
            "compute.spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html",
            "credentials.ovn-metadata-proxy-shared-secret": metadata,
            "identity.admin-role": None,
            "identity.auth-url": "http://10.153.2.45:80/openstack-keystone",
            "identity.password": "user-password",
            "identity.project-domain-id": "pdomain-id",
            "identity.project-domain-name": "pdomain_-ame",
            "identity.project-id": "uproj-id",
            "identity.project-name": "user-project",
            "identity.region-name": "region12",
            "identity.user-domain-id": "udomain-id",
            "identity.user-domain-name": "udomain-name",
            "identity.username": "username",
            "logging.debug": False,
            "monitoring.enable": False,
            "network.dns-servers": "8.8.8.8",
            "network.external-bridge": "br-ex",
            "network.external-bridge-address": "10.20.20.1/24",
            "network.ip-address": "10.0.0.10",
            "network.ovn-cacert": cacert_with_intermediates,
            "network.ovn-cert": certificate,
            "network.ovn-key": private_key,
            "network.ovn-sb-connection": "ssl:10.15.24.37:6642",
            "network.physnet-name": "physnet1",
            "node.fqdn": "test.local",
            "node.ip-address": "10.0.0.10",
            "rabbitmq.url": "rabbit://hypervisor:rabbit.pass@rabbithost1.local:5672/openstack",
            "telemetry.enable": False,
            "ca.bundle": None,
            "masakari.enable": False,
            "sev.reserved-host-memory-mb": None,
        }
        hypervisor_snap_mock.set.assert_any_call(expect_settings, typed=True)

    def test_all_relations(self):
        """Test all the charms relations."""
        # Add cos-agent relation
        self.harness.add_relation(
            "cos-agent",
            "grafana-agent",
            unit_data={
                "config": '{"metrics_alert_rules": {}, "log_alert_rules": {}, "dashboards": ["/Td6WFoAAATm1rRGAAAAABzfRCEftvN9AQAAAAAEWVo="], "metrics_scrape_jobs": [{"metrics_path": "/metrics", "static_configs": [{"targets": ["localhost:9177"]}]], "log_slots": []}',
                "egress-subnets": "10.1.171.64/32",
                "ingress-address": "10.1.171.64",
                "private-address": "10.1.171.64",
            },
        )

        # Add ceilometer-service relation
        self.harness.add_relation(
            "ceilometer-service",
            "ceilometer",
            app_data={"telemetry-secret": "FAKE_SECRET"},
        )

        # Add nova-service relation
        self.harness.add_relation(
            "nova-service",
            "nova",
            app_data={
                "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spiceauto.html"
            },
        )

        # Add masakari-service relation
        self.harness.add_relation(
            "masakari-service",
            "masakari",
            app_data={"ready": "true"},
        )

        self.get_local_ip_by_default_route.return_value = "10.0.0.10"
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        self.snap.SnapState.Latest = "latest"
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        self.socket.getfqdn.return_value = "test.local"
        self.socket.gethostname.return_value = "test"
        self.initial_setup()
        self.harness.set_leader()
        test_utils.add_complete_amqp_relation(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        hypervisor_snap_mock.ensure.assert_any_call(
            "latest", channel="essex/stable"
        )
        metadata = self.harness.charm.metadata_secret()
        cacert = test_utils.TEST_CA
        cacert_with_intermediates = (
            test_utils.TEST_CA + "\n" + "\n".join(test_utils.TEST_CHAIN)
        )
        cacert = base64.b64encode(cacert.encode()).decode()
        cacert_with_intermediates = base64.b64encode(
            cacert_with_intermediates.encode()
        ).decode()
        private_key = base64.b64encode(
            self.harness.charm.contexts().certificates.key.encode()
        ).decode()
        certificate = base64.b64encode(
            test_utils.TEST_SERVER_CERT.encode()
        ).decode()
        expect_settings = {
            "compute.cpu-mode": "host-model",
            "compute.spice-proxy-address": "10.0.0.10",
            "compute.cacert": cacert,
            "compute.cert": certificate,
            "compute.key": private_key,
            "compute.migration-address": "10.0.0.10",
            "compute.resume-on-boot": True,
            "compute.pci-device-specs": None,
            "compute.rbd-user": "nova",
            "compute.rbd-secret-uuid": "ddd",
            "compute.rbd-key": "eee",
            "compute.spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spiceauto.html",
            "credentials.ovn-metadata-proxy-shared-secret": metadata,
            "identity.admin-role": None,
            "identity.auth-url": "http://10.153.2.45:80/openstack-keystone",
            "identity.password": "user-password",
            "identity.project-domain-id": "pdomain-id",
            "identity.project-domain-name": "pdomain_-ame",
            "identity.project-id": "uproj-id",
            "identity.project-name": "user-project",
            "identity.region-name": "region12",
            "identity.user-domain-id": "udomain-id",
            "identity.user-domain-name": "udomain-name",
            "identity.username": "username",
            "logging.debug": False,
            "monitoring.enable": True,
            "network.dns-servers": "8.8.8.8",
            "network.external-bridge": "br-ex",
            "network.external-bridge-address": "10.20.20.1/24",
            "network.ip-address": "10.0.0.10",
            "network.ovn-cacert": cacert_with_intermediates,
            "network.ovn-cert": certificate,
            "network.ovn-key": private_key,
            "network.ovn-sb-connection": "ssl:10.15.24.37:6642",
            "network.physnet-name": "physnet1",
            "node.fqdn": "test.local",
            "node.ip-address": "10.0.0.10",
            "rabbitmq.url": "rabbit://hypervisor:rabbit.pass@rabbithost1.local:5672/openstack",
            "telemetry.enable": True,
            "telemetry.publisher-secret": "FAKE_SECRET",
            "ca.bundle": None,
            "masakari.enable": True,
            "sev.reserved-host-memory-mb": None,
        }
        hypervisor_snap_mock.set.assert_any_call(expect_settings, typed=True)

    def test_openstack_hypervisor_snap_not_installed(self):
        """Check action raises SnapNotFoundError if openstack-hypervisor snap is not installed."""
        self.harness.begin()
        self.snap.SnapCache.side_effect = snap.SnapNotFoundError
        with self.assertRaises(snap.SnapNotFoundError):
            self.harness.run_action("list-nics")

    def test_list_nics_snap_not_installed(self):
        """Check action raises ActionFailed if snap is not installed."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = False
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        with self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("list-nics")

    def test_list_nics(self):
        """Check action returns nics."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        subprocess_run_mock = MagicMock()
        subprocess_run_mock.return_value = MagicMock(
            stdout=bytes(
                json.dumps({"nics": ["eth0", "eth1"], "candidates": ["eth2"]}),
                "utf-8",
            ),
            stderr=b"yes things went well",
            returncode=0,
        )
        self.subprocess.run = subprocess_run_mock
        action_output = self.harness.run_action("list-nics")
        assert "candidates" in action_output.results["result"]

    def test_list_nics_error(self):
        """Check action raises ActionFailed if subprocess returns non-zero."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        subprocess_run_mock = MagicMock()
        subprocess_run_mock.return_value = MagicMock(
            stdout=b"",
            stderr=b"things did not go well",
            returncode=1,
        )
        self.subprocess.run = subprocess_run_mock
        with self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("list-nics")

    def test_list_flavors(self):
        """Check action return flavors."""
        flavors = "flavor1,flavor2"
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = False
        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }
        hypervisor_snap_mock.get.return_value = flavors
        action_output = self.harness.run_action("list-flavors")
        assert action_output.results["result"] == flavors

    def test_snap_connect_success(self):
        """Test successful snap connect to epa-orchestrator."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.return_value = None

        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        self.harness.charm._connect_to_epa_orchestrator()
        hypervisor_snap_mock.connect.assert_called_once_with(
            charm.EPA_INFO_PLUG, slot=charm.EPA_INFO_SLOT
        )

    def test_snap_connect_failure_snaperror(self):
        """Test snap connect failure with SnapError."""
        self.harness.begin()
        hypervisor_snap_mock = MagicMock()
        hypervisor_snap_mock.present = True
        epa_orchestrator_snap_mock = MagicMock()
        epa_orchestrator_snap_mock.present = True
        hypervisor_snap_mock.connect.side_effect = snap.SnapError(
            "Connection failed"
        )

        self.snap.SnapCache.return_value = {
            "openstack-hypervisor": hypervisor_snap_mock,
            "epa-orchestrator": epa_orchestrator_snap_mock,
        }

        with self.assertRaises(snap.SnapError):
            self.harness.charm._connect_to_epa_orchestrator()

    def test_check_system_services_raises_when_ovs_running(self):
        """Test check_system_services raises BlockedExceptionError if OVS is running."""
        self.service_running.return_value = True
        self.harness.begin()
        with self.assertRaises(sunbeam_guard.BlockedExceptionError):
            self.harness.charm.check_system_services()

    def test_check_system_services_passes_when_ovs_not_running(self):
        """Test check_system_services does nothing if OVS is not running."""
        self.service_running.return_value = False
        self.harness.begin()
        # Should not raise
        self.harness.charm.check_system_services()
