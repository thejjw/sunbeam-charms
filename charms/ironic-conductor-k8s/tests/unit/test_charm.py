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

"""Unit tests for the Ironic Conductor K8s charm."""

import unittest.mock as mock

import api_utils
import charm
import ops_sunbeam.test_utils as test_utils
from keystoneauth1 import exceptions as ks_exc
from ops import (
    model,
)
from ops.testing import (
    ActionFailed,
    Harness,
)
from ops_sunbeam import (
    k8s_resource_handlers,
)


class _IronicConductorOperatorCharm(charm.IronicConductorOperatorCharm):
    """Test implementation of Ironic Conductor operator."""

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


class TestIronicConductorOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Ironic Conductor Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)

        patcher = mock.patch.object(k8s_resource_handlers, "Client")
        mock_client = patcher.start()
        self.addCleanup(patcher.stop)

        client = mock_client.return_value
        svc = client.get.return_value
        svc.status.loadBalancer.ingress = [mock.Mock(ip="foo.lish")]

        self.harness = test_utils.get_harness(
            _IronicConductorOperatorCharm, container_calls=self.container_calls
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequiresEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def _check_file_contents(self, container, path, strings):
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore

        with client.pull(path) as infile:
            received_data = infile.read()

        for string in strings:
            self.assertIn(string, received_data)

    def add_db_relation(self, harness: Harness, name: str) -> str:
        """Add db relation."""
        rel_id = harness.add_relation(name, "mysql")
        harness.add_relation_unit(rel_id, "mysql/0")
        harness.update_relation_data(
            rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
        )
        return rel_id

    def add_ceph_rgw_relation(self):
        """Add ceph-rgw-ready relation."""
        return self.harness.add_relation(
            charm.CEPH_RGW_RELATION,
            "microceph",
            app_data={"ready": "true"},
        )

    def test_pebble_ready_handler(self):
        """Test pebble ready event handling."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("ironic-conductor")
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_all_relations(self, mock_create_ks_session, mock_osclients):
        """Test all integrations for operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        self.add_ceph_rgw_relation()

        # This action needs to be run, otherwise the charm is Blocked.
        os_cli = mock_osclients.return_value
        os_cli.glance_stores = ["swift"]
        action_event = self.harness.run_action("set-temp-url-secret")
        self.assertEqual(
            "Temp URL secret set.", action_event.results.get("output")
        )

        charm_status = self.harness.charm.status
        self.assertIsInstance(charm_status.status, model.ActiveStatus)

        setup_cmds = [
            ["a2ensite", "wsgi-ironic-conductor"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["ironic-conductor"])

        config_files = [
            "/etc/apache2/sites-available/wsgi-ironic-conductor.conf",
            "/etc/ironic/ironic.conf",
            "/etc/ironic/rootwrap.conf",
            "/tftpboot/map-file",
            "/tftpboot/grub/grub.cfg",
        ]
        for f in config_files:
            self.check_file("ironic-conductor", f)

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_charm_invalid_config(
        self, mock_create_ks_session, mock_osclients
    ):
        """Test the charm configuration validation."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        self.add_ceph_rgw_relation()

        os_cli = mock_osclients.return_value
        os_cli.glance_stores = ["swift"]
        action_event = self.harness.run_action("set-temp-url-secret")

        self.assertEqual(
            "Temp URL secret set.", action_event.results.get("output")
        )
        charm_status = self.harness.charm.status
        self.assertIsInstance(charm_status.status, model.ActiveStatus)

        # invalid default-network-interface.
        cfg = {"default-network-interface": "foo"}
        self.harness.update_config(cfg)

        self.assertIsInstance(charm_status.status, model.BlockedStatus)

        # invalid enabled-network-interfaces.
        cfg = {
            "default-network-interface": "flat",
            "enabled-network-interfaces": "foo",
        }
        self.harness.update_config(cfg)

        self.assertIsInstance(charm_status.status, model.BlockedStatus)

        # invalid enabled-hw-types.
        cfg = {"enabled-network-interfaces": "flat", "enabled-hw-types": "foo"}
        self.harness.update_config(cfg)

        self.assertIsInstance(charm_status.status, model.BlockedStatus)

        # valid configuration.
        cfg = {"enabled-hw-types": "ipmi"}
        self.harness.update_config(cfg)

        self.assertIsInstance(charm_status.status, model.ActiveStatus)

    @mock.patch("api_utils.OSClients")
    @mock.patch("api_utils.create_keystone_session")
    def test_charm_configuration(self, mock_create_ks_session, mock_osclients):
        """Test the charm configuration."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)

        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        self.add_ceph_rgw_relation()

        os_cli = mock_osclients.return_value
        os_cli.glance_stores = ["swift"]
        action_event = self.harness.run_action("set-temp-url-secret")

        self.assertEqual(
            "Temp URL secret set.", action_event.results.get("output")
        )
        charm_status = self.harness.charm.status
        self.assertIsInstance(charm_status.status, model.ActiveStatus)

        # check ironic_config context with default configuration.
        lines = [
            "interfaces = internal",
            "enabled_bios_interfaces = no-bios",
            "enabled_boot_interfaces = pxe",
            "enabled_console_interfaces = ipmitool-shellinabox, ipmitool-socat, no-console",
            "enabled_deploy_interfaces = direct",
            "enabled_hardware_types = intel-ipmi, ipmi",
            "enabled_inspect_interfaces = no-inspect",
            "enabled_management_interfaces = intel-ipmitool, ipmitool, noop",
            "enabled_power_interfaces = ipmitool",
            "enabled_raid_interfaces = no-raid",
            "enabled_vendor_interfaces = ipmitool, no-vendor",
            "[hardware_type:intel-ipmi]",
            "[hardware_type:ipmi]",
            "default_deploy_interface = direct",
            "http_url=http://foo.lish:80",
            "tftp_server = foo.lish",
        ]
        self._check_file_contents(
            "ironic-conductor", "/etc/ironic/ironic.conf", lines
        )

        cfg = {"enabled-hw-types": "fake,ipmi,redfish,idrac"}
        self.harness.update_config(cfg)

        lines = [
            "enabled_bios_interfaces = fake, idrac-wsman, no-bios",
            "enabled_boot_interfaces = fake, pxe, redfish-virtual-media",
            "enabled_console_interfaces = fake, ipmitool-shellinabox, ipmitool-socat, no-console",
            "enabled_deploy_interfaces = direct, fake",
            "enabled_hardware_types = fake-hardware, idrac, intel-ipmi, ipmi, redfish",
            "enabled_inspect_interfaces = fake, idrac-redfish, redfish, no-inspect",
            "enabled_management_interfaces = fake, idrac-redfish, intel-ipmitool, ipmitool, redfish, noop",
            "enabled_power_interfaces = fake, idrac-redfish, ipmitool, redfish",
            "enabled_raid_interfaces = fake, idrac-wsman, no-raid",
            "enabled_vendor_interfaces = fake, idrac-wsman, ipmitool, no-vendor",
            "[hardware_type:fake-hardware]",
            "default_deploy_interface = fake",
            "[hardware_type:idrac]",
            "[hardware_type:intel-ipmi]",
            "[hardware_type:ipmi]",
            "[hardware_type:redfish]",
            "default_deploy_interface = direct",
        ]
        self._check_file_contents(
            "ironic-conductor", "/etc/ironic/ironic.conf", lines
        )

        # check other configurations.
        cfg = {"cleaning-network": "foo", "provisioning-network": "lish"}
        self.harness.update_config(cfg)

        secret = self.harness.charm.leader_get("temp_url_secret")
        lines = [
            "cleaning_network = foo",
            "provisioning_network = lish",
            f"swift_temp_url_key = {secret}",
            "swift_temp_url_duration = 1200",
        ]
        self._check_file_contents(
            "ironic-conductor", "/etc/ironic/ironic.conf", lines
        )

    @mock.patch("keystoneclient.v3.Client")
    @mock.patch("swiftclient.Connection")
    @mock.patch("glanceclient.Client")
    @mock.patch("keystoneauth1.session.Session")
    @mock.patch("keystoneauth1.loading.get_plugin_loader")
    def test_set_temp_url_secret_failures(
        self,
        mock_get_plugin_loader,
        mock_session,
        mock_glance_client,
        mock_swift_client,
        mock_keystone_client,
    ):
        """Test the set-temp-url-secret action failures."""
        # Run the action - should fail since it's not the leader.
        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertEqual(
            "action must be run on the leader unit.", str(ctx.exception)
        )

        # Set the leader, rerun the action, but relations are not set.
        test_utils.set_all_pebbles_ready(self.harness)
        self.harness.set_leader()

        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertIn(
            "required relations are not yet available", str(ctx.exception)
        )

        # This adds all the default / common relations.
        # Test the keystone session creation case.
        mock_session.side_effect = Exception("to be expected.")
        test_utils.add_all_relations(self.harness)
        self.add_ceph_rgw_relation()

        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertIn("failed to create keystone session", str(ctx.exception))
        mock_get_plugin_loader.assert_called_once_with("v3password")
        mock_loader = mock_get_plugin_loader.return_value
        mock_loader.load_from_options.assert_called_once_with(
            username="username",
            password="user-password",
            project_name="user-project",
            auth_url="http://10.153.2.45:80/openstack-keystone",
            project_domain_name="pdomain_-ame",
            user_domain_name="udomain-name",
        )
        mock_session.assert_called_once_with(
            auth=mock_loader.load_from_options.return_value,
            verify=api_utils.SYSTEM_CA_BUNDLE,
        )

        # Swift not yet available.
        mock_session.side_effect = None
        mock_keystone_client.return_value.endpoints.find.side_effect = (
            ks_exc.http.NotFound
        )

        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertIn("Swift not yet available.", str(ctx.exception))
        mock_glance_client.assert_called_once_with(
            session=mock_session.return_value,
            version=2,
        )
        mock_swift_client.assert_called_once_with(
            session=mock_session.return_value,
            cacert=api_utils.SYSTEM_CA_BUNDLE,
        )
        mock_keystone_client.assert_called_once_with(
            session=mock_session.return_value,
        )

        # Glance not yet available.
        mock_keystone_client.return_value.endpoints.find.side_effect = [
            mock.sentinel.swift_service,
            ks_exc.http.NotFound,
        ]

        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertIn("Glance not yet available.", str(ctx.exception))

        # Glance store does not have Swift storage backend.
        mock_keystone_client.return_value.endpoints.find.side_effect = None

        with self.assertRaises(ActionFailed) as ctx:
            self.harness.run_action("set-temp-url-secret")

        self.assertIn(
            "Glance does not support Swift storage backend.",
            str(ctx.exception),
        )

    @mock.patch("keystoneclient.v3.Client")
    @mock.patch("swiftclient.Connection")
    @mock.patch("glanceclient.Client")
    @mock.patch("keystoneauth1.session.Session")
    @mock.patch("keystoneauth1.loading.get_plugin_loader")
    def test_set_temp_url_secret(
        self,
        mock_get_plugin_loader,
        mock_session,
        mock_glance_client,
        mock_swift_client,
        mock_keystone_client,
    ):
        """Test the set-temp-url-secret action."""
        # Set the leader, add all the default / common relations.
        test_utils.set_all_pebbles_ready(self.harness)
        self.harness.set_leader()
        test_utils.add_all_relations(self.harness)
        self.add_ceph_rgw_relation()

        mock_glance_client.return_value.images.get_stores_info.return_value = {
            "stores": [
                {"id": "swift"},
            ],
        }

        # Create secret.
        action_event = self.harness.run_action("set-temp-url-secret")

        self.assertEqual(
            "Temp URL secret set.", action_event.results.get("output")
        )
        secret = self.harness.charm.leader_get("temp_url_secret")
        mock_swift_client.return_value.post_account.assert_called_once_with(
            {"x-account-meta-temp-url-key": secret},
        )

        # Secret already exists.
        mock_swift_client.return_value.post_account.reset_mock()
        secret = self.harness.charm.leader_get("temp_url_secret")
        mock_swift_client.return_value.get_account.return_value = [
            {
                "x-account-meta-temp-url-key": secret,
            },
        ]

        action_event = self.harness.run_action("set-temp-url-secret")

        self.assertEqual(
            "Temp URL secret set.", action_event.results.get("output")
        )
        mock_swift_client.return_value.post_account.assert_not_called()
