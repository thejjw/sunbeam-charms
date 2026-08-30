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

"""Test aso."""

import json
import os
import sys
from unittest.mock import (
    MagicMock,
    PropertyMock,
    patch,
)

sys.path.append("tests/lib")  # noqa
sys.path.append("src")  # noqa

import ops.model
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.test_utils as test_utils

from . import (
    test_charms,
)


class TestOSBaseOperatorCharm(test_utils.CharmTestCase):
    """Test for the OSBaseOperatorCharm class."""

    PATCHES = []

    def setUp(self) -> None:
        """Charm test class setup."""
        self.container_calls = test_utils.ContainerCalls()
        super().setUp(sunbeam_charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.harness = test_utils.get_harness(
            test_charms.MyCharm,
            test_charms.CHARM_METADATA,
            self.container_calls,
            charm_config=test_charms.CHARM_CONFIG,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
        )
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    def test_write_config(self) -> None:
        """Test writing config when charm is ready."""
        self.assertEqual(self.container_calls.push["my-service"], [])

    def test_relation_handlers_ready(self) -> None:
        """Test relation handlers are ready."""
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

    def test_configure_charm_marks_completed(self) -> None:
        """Test successful charm configuration records completion."""
        charm = self.harness.charm
        with patch.object(charm, "update_relations"), patch.object(
            charm, "configure_unit"
        ), patch.object(charm, "configure_app"), patch.object(
            charm, "post_config_setup"
        ):
            charm.configure_charm(self.mock_event)

        self.assertTrue(charm._configure_charm_completed)

    def test_configure_charm_does_not_mark_guarded_failure_completed(
        self,
    ) -> None:
        """Test a configuration failure consumed by guard remains incomplete."""
        charm = self.harness.charm
        with patch.object(charm, "update_relations"), patch.object(
            charm,
            "configure_unit",
            side_effect=sunbeam_charm.sunbeam_guard.WaitingExceptionError(
                "not ready"
            ),
        ):
            charm.configure_charm(self.mock_event)

        self.assertFalse(charm._configure_charm_completed)


class TestOSBaseOperatorCharmK8S(test_utils.CharmTestCase):
    """Test for the OSBaseOperatorCharm class."""

    PATCHES = []

    def setUp(self) -> None:
        """Charm test class setup."""
        self.container_calls = test_utils.ContainerCalls()
        super().setUp(sunbeam_charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            test_charms.MyCharmK8S,
            test_charms.CHARM_METADATA_K8S,
            self.container_calls,
            charm_config=test_charms.CHARM_CONFIG,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
        )
        self.mock_event = MagicMock()
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    def set_pebble_ready(self) -> None:
        """Set pebble ready event."""
        self.harness.container_pebble_ready("my-service")

    def test_pebble_ready_handler(self) -> None:
        """Test is raised and observed."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.set_pebble_ready()
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_write_config(self) -> None:
        """Test writing config when charm is ready."""
        self.set_pebble_ready()
        self.assertEqual(self.container_calls.push["my-service"], [])

    def test_container_names(self) -> None:
        """Test container name list is correct."""
        self.assertEqual(self.harness.charm.container_names, ["my-service"])

    def test_relation_handlers_ready(self) -> None:
        """Test relation handlers are ready."""
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )


class _TestOSBaseOperatorAPICharm(test_utils.CharmTestCase):
    """Test for the OSBaseOperatorAPICharm class."""

    PATCHES = []

    def setUp(self, charm_to_test: test_charms.MyAPICharm) -> None:
        """Charm test class setup."""
        self.container_calls = test_utils.ContainerCalls()

        super().setUp(sunbeam_charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.harness = test_utils.get_harness(
            charm_to_test,
            test_charms.API_CHARM_METADATA,
            self.container_calls,
            charm_config=test_charms.CHARM_CONFIG,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
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

    def set_pebble_ready(self) -> None:
        """Set pebble ready event."""
        self.harness.container_pebble_ready("my-service")


class TestOSBaseOperatorAPICharm(_TestOSBaseOperatorAPICharm):
    """Test Charm with services."""

    def setUp(self) -> None:
        """Run test class setup."""
        super().setUp(test_charms.MyAPICharm)

    def test_write_config(self) -> None:
        """Test when charm is ready configs are written correctly."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.set_pebble_ready()
        self.harness.charm.leader_set({"foo": "bar"})
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        expect_entries = [
            "/bin/wsgi_admin",
            "hardpassword",
            "True",
            "rabbit://my-service:rabbit.pass@rabbithost1.local:5672/openstack",
            "rabbithost1.local",
            "svcpass1",
            "bar",
        ]
        expect_string = "\n" + "\n".join(expect_entries)
        self.harness.set_can_connect("my-service", True)
        effective_user_id = os.geteuid()
        effective_group_id = os.getegid()
        self.check_file(
            "my-service",
            "/etc/my-service/my-service.conf",
            contents=expect_string,
            user=effective_user_id,
            group=effective_group_id,
        )
        self.check_file(
            "my-service",
            "/etc/apache2/sites-available/wsgi-my-service.conf",
            contents=expect_string,
            user=effective_user_id,
            group=effective_group_id,
        )

    def test_assess_status(self) -> None:
        """Test charm is setting status correctly."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.charm.leader_set({"foo": "bar"})
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        self.harness.set_can_connect("my-service", True)
        self.assertNotEqual(
            self.harness.charm.status.status, ops.model.ActiveStatus()
        )
        self.set_pebble_ready()
        for ph in self.harness.charm.pebble_handlers:
            self.assertTrue(ph.service_ready)

        self.assertEqual(
            self.harness.charm.status.status, ops.model.ActiveStatus()
        )

    def test_start_services(self) -> None:
        """Test service is started."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.set_pebble_ready()
        self.harness.charm.leader_set({"foo": "bar"})
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        self.harness.set_can_connect("my-service", True)
        self.assertEqual(
            self.container_calls.started_services("my-service"),
            ["wsgi-my-service"],
        )

    def test__on_database_changed(self) -> None:
        """Test database is requested."""
        rel_id = self.harness.add_relation("peers", "my-service")
        self.harness.add_relation_unit(rel_id, "my-service/1")
        self.harness.set_leader()
        self.set_pebble_ready()
        db_rel_id = test_utils.add_base_db_relation(self.harness)
        test_utils.add_db_relation_credentials(self.harness, db_rel_id)
        rel_data = self.harness.get_relation_data(db_rel_id, "my-service")
        requested_db = rel_data["database"]
        self.assertEqual(requested_db, "my_service")

    def test_contexts(self) -> None:
        """Test contexts are correctly populated."""
        rel_id = self.harness.add_relation("peers", "my-service")
        self.harness.add_relation_unit(rel_id, "my-service/1")
        self.harness.set_leader()
        self.set_pebble_ready()
        db_rel_id = test_utils.add_base_db_relation(self.harness)
        test_utils.add_db_relation_credentials(self.harness, db_rel_id)
        contexts = self.harness.charm.contexts()
        self.assertEqual(
            contexts.wsgi_config.wsgi_admin_script, "/bin/wsgi_admin"
        )
        self.assertEqual(contexts.database.database_password, "hardpassword")
        self.assertEqual(contexts.options.debug, True)

    def test_peer_leader_db(self) -> None:
        """Test interacting with peer app db."""
        rel_id = self.harness.add_relation("peers", "my-service")
        self.harness.add_relation_unit(rel_id, "my-service/1")
        self.harness.set_leader()
        self.harness.charm.leader_set({"ready": "true"})
        self.harness.charm.leader_set({"foo": "bar"})
        self.harness.charm.leader_set(ginger="biscuit")
        rel_data = self.harness.get_relation_data(rel_id, "my-service")
        self.assertEqual(
            rel_data, {"ready": "true", "foo": "bar", "ginger": "biscuit"}
        )
        self.assertEqual(self.harness.charm.leader_get("ready"), "true")
        self.assertEqual(self.harness.charm.leader_get("foo"), "bar")
        self.assertEqual(self.harness.charm.leader_get("ginger"), "biscuit")

    def test_peer_unit_data(self) -> None:
        """Test interacting with peer app db."""
        rel_id = self.harness.add_relation("peers", "my-service")
        self.harness.add_relation_unit(rel_id, "my-service/1")
        self.harness.update_relation_data(
            rel_id, "my-service/1", {"today": "monday"}
        )
        self.assertEqual(
            self.harness.charm.peers.get_all_unit_values(
                "today",
                include_local_unit=False,
            ),
            ["monday"],
        )
        self.assertEqual(
            self.harness.charm.peers.get_all_unit_values(
                "today",
                include_local_unit=True,
            ),
            ["monday"],
        )
        self.harness.charm.peers.set_unit_data({"today": "friday"})
        self.assertEqual(
            self.harness.charm.peers.get_all_unit_values(
                "today",
                include_local_unit=False,
            ),
            ["monday"],
        )
        self.assertEqual(
            self.harness.charm.peers.get_all_unit_values(
                "today",
                include_local_unit=True,
            ),
            ["monday", "friday"],
        )

    def test_peer_leader_ready(self) -> None:
        """Test peer leader ready methods."""
        rel_id = self.harness.add_relation("peers", "my-service")
        self.harness.add_relation_unit(rel_id, "my-service/1")
        self.harness.set_leader()
        self.assertFalse(self.harness.charm.is_leader_ready())
        self.harness.charm.set_leader_ready()
        self.assertTrue(self.harness.charm.is_leader_ready())

    def test_endpoint_urls(self) -> None:
        """Test public_url and internal_url properties."""
        # Add ingress relation
        test_utils.add_complete_ingress_relation(self.harness)
        self.assertEqual(
            self.harness.charm.internal_url, "http://internal-url:80/"
        )
        self.assertEqual(
            self.harness.charm.public_url, "http://public-url:80/"
        )

    @patch("lightkube.core.client.Client")
    def test_endpoint_urls_no_ingress(self, mock_client: patch) -> None:
        """Test public_url and internal_url with no ingress defined."""

        class MockService:
            """Mock lightkube client service object."""

            def __init__(self) -> None:
                self.status = None

        mock_client.return_value = MagicMock()
        mock_client.return_value.get.return_value = MockService()
        self.assertEqual(
            self.harness.charm.internal_url, "http://10.0.0.10:789"
        )
        self.assertEqual(self.harness.charm.public_url, "http://10.0.0.10:789")

    def test_relation_handlers_ready(self) -> None:
        """Test relation handlers are ready."""
        # Add all mandatory relations and test relation_handlers_ready
        db_rel_id = test_utils.add_base_db_relation(self.harness)
        test_utils.add_db_relation_credentials(self.harness, db_rel_id)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            {"identity-service", "ingress-internal", "amqp"},
        )

        amqp_rel_id = test_utils.add_base_amqp_relation(self.harness)
        test_utils.add_amqp_relation_credentials(self.harness, amqp_rel_id)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            {"ingress-internal", "identity-service"},
        )

        identity_rel_id = test_utils.add_base_identity_service_relation(
            self.harness
        )
        test_utils.add_identity_service_relation_response(
            self.harness, identity_rel_id
        )
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            {"ingress-internal"},
        )

        ingress_rel_id = test_utils.add_ingress_relation(
            self.harness, "internal"
        )
        test_utils.add_ingress_relation_data(
            self.harness, ingress_rel_id, "internal"
        )

        ceph_access_rel_id = test_utils.add_base_ceph_access_relation(
            self.harness
        )
        test_utils.add_ceph_access_relation_response(
            self.harness, ceph_access_rel_id
        )
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

        # Add an optional relation and test if relation_handlers_ready
        # returns True
        optional_rel_id = test_utils.add_ingress_relation(
            self.harness, "public"
        )
        test_utils.add_ingress_relation_data(
            self.harness, optional_rel_id, "public"
        )
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

        # Remove a mandatory relation and test if relation_handlers_ready
        # returns False
        self.harness.remove_relation(ingress_rel_id)
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            {"ingress-internal"},
        )

        # Add the mandatory relation back and retest relation_handlers_ready
        ingress_rel_id = test_utils.add_ingress_relation(
            self.harness, "internal"
        )
        test_utils.add_ingress_relation_data(
            self.harness, ingress_rel_id, "internal"
        )
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

    def test_add_explicit_port(self):
        """Test add_explicit_port method."""
        self.assertEqual(
            self.harness.charm.add_explicit_port("http://test.org/something"),
            "http://test.org:80/something",
        )
        self.assertEqual(
            self.harness.charm.add_explicit_port(
                "http://test.org:80/something"
            ),
            "http://test.org:80/something",
        )
        self.assertEqual(
            self.harness.charm.add_explicit_port("https://test.org/something"),
            "https://test.org:443/something",
        )
        self.assertEqual(
            self.harness.charm.add_explicit_port(
                "https://test.org:443/something"
            ),
            "https://test.org:443/something",
        )
        self.assertEqual(
            self.harness.charm.add_explicit_port(
                "http://test.org:8080/something"
            ),
            "http://test.org:8080/something",
        )
        self.assertEqual(
            self.harness.charm.add_explicit_port(
                "https://test.org:8443/something"
            ),
            "https://test.org:8443/something",
        )

    def test_admin_url_id_svc(self):
        """Test admin_url with service ID."""
        test_utils.add_complete_identity_relation(self.harness)
        self.assertEqual(
            self.harness.charm.admin_url,
            "http://10.0.0.10:789",
        )

    def test_admin_url_fallback_to_service_dns(self):
        """Test admin_url fallback to service DNS."""
        with patch.object(
            self.harness.charm.model,
            "get_binding",
            MagicMock(return_value=None),
        ):
            self.assertEqual(
                self.harness.charm.admin_url,
                "http://my-service.test-model.svc:789",
            )

    def test_internal_url_ingress_internal(self):
        """Test internal_url with internal ingress."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.assertEqual(
            self.harness.charm.internal_url,
            "http://internal-url:80/",
        )

    def test_internal_url_fallback_to_id_svc(self):
        """Test internal_url with service ID."""
        test_utils.add_complete_identity_relation(self.harness)
        self.assertEqual(
            self.harness.charm.internal_url,
            "http://10.0.0.10:789",
        )

    def test_internal_url_fallback_to_service_dns(self):
        """Test internal fallback to service DNS."""
        with patch.object(
            self.harness.charm.model,
            "get_binding",
            MagicMock(return_value=None),
        ):
            self.assertEqual(
                self.harness.charm.internal_url,
                "http://my-service.test-model.svc:789",
            )

    def test_public_url_ingress_public(self):
        """Test public_url with public ingress."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.assertEqual(
            self.harness.charm.public_url,
            "http://public-url:80/",
        )

    def test_public_url_fallback_to_internal(self):
        """Test public_url fallback to internal."""
        self.assertEqual(
            self.harness.charm.public_url,
            self.harness.charm.internal_url,
        )

    def test_public_url_attribute_error(self):
        """Test public_url with attribute error."""
        del self.harness.charm.ingress_public
        self.assertEqual(
            self.harness.charm.public_url,
            self.harness.charm.internal_url,
        )


class TestOSBaseOperatorAPICharmIdentityExtraRoles(
    _TestOSBaseOperatorAPICharm
):
    """Test identity-service extra-role requests."""

    def setUp(self) -> None:
        """Run test class setup."""
        super().setUp(test_charms.MyAPICharmWithIdentityExtraRoles)

    def test_identity_service_request_includes_extra_roles(self) -> None:
        """Charm-declared extra roles are published on identity-service."""
        self.harness.set_leader()

        identity_rel_id = test_utils.add_base_identity_service_relation(
            self.harness
        )
        local_data = self.harness.get_relation_data(
            identity_rel_id, self.harness.charm.app
        )

        self.assertEqual(
            json.loads(local_data["extra-roles"]),
            ["reader", "load-balancer_observer"],
        )


class TestOSBaseOperatorMultiSVCAPICharm(_TestOSBaseOperatorAPICharm):
    """Test Charm with multiple services."""

    def setUp(self) -> None:
        """Charm test class setup."""
        super().setUp(test_charms.TestMultiSvcCharm)

    def test_start_services(self) -> None:
        """Test multiple services are started."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.set_pebble_ready()
        self.harness.charm.leader_set({"foo": "bar"})
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        self.harness.set_can_connect("my-service", True)
        self.assertEqual(
            sorted(self.container_calls.started_services("my-service")),
            sorted(["apache forwarder", "my-service"]),
        )


class TestOSBaseOperatorCharmSnap(test_utils.CharmTestCase):
    """Test snap based charm."""

    PATCHES = []

    SNAP_CHARM_ACTIONS = """
refresh-snap:
  description: Refresh snap to latest on configured channel.
"""

    def setUp(self) -> None:
        """Charm test class setup."""
        super().setUp(sunbeam_charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            test_charms.MySnapCharm,
            test_charms.CHARM_METADATA,
            None,
            charm_config=test_charms.CHARM_CONFIG,
            charm_actions=self.SNAP_CHARM_ACTIONS,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
        )
        self.mock_event = MagicMock()
        # ActionEvent.params is a dict on real events; use a real dict so
        # params.get("channel") returns None when unset.
        self.mock_event.params = {}
        self.harness.begin()
        self.addCleanup(self.harness.cleanup)

    def test_snap_name_property(self):
        """Test snap_name property returns configured snap name."""
        self.assertEqual(self.harness.charm.snap_name, "mysnap")

    def test_snap_channel_property_default(self):
        """Test snap_channel property returns default value."""
        self.assertEqual(self.harness.charm.snap_channel, "latest/stable")

    def test_set_snap_data(self) -> None:
        """Test snap set data."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.get.return_value = {
            "settings.debug": False,
            "settings.region": "RegionOne",
        }
        charm.set_snap_data({"settings.debug": True})
        snap.set.assert_called_once_with({"settings.debug": True}, typed=True)

    def test_set_snap_data_namespace(self) -> None:
        """Test snap set data under namespace."""
        charm = self.harness.charm
        snap = charm.mock_snap
        namespace = "ceph.monostack"
        snap.get.return_value = {
            "auth": "cephx",
        }
        # check unsetting a non-existent value is passed as None
        new_data = {"key": "abc", "value": None}
        charm.set_snap_data(new_data, namespace=namespace)
        snap.get.assert_called_once_with(namespace, typed=True)
        snap.set.assert_called_once_with(
            {namespace: {"key": "abc"}}, typed=True
        )

    def test_get_snap_returns_installed_parallel_instance(self) -> None:
        """Return installed parallel snap instance from SnapCache."""
        charm = self.harness.charm
        installed_snap = MagicMock()
        cache = MagicMock()
        cache.__getitem__.return_value = installed_snap
        charm.snap_module.SnapCache.return_value = cache

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_noha",
        ):
            result = sunbeam_charm.OSBaseOperatorCharmSnap.get_snap(charm)

        self.assertIs(result, installed_snap)
        cache.__getitem__.assert_called_once_with("mysnap_noha")
        cache._snap_client.get_snap_information.assert_not_called()
        charm.snap_module.Snap.assert_not_called()

    def test_get_snap_builds_available_parallel_instance_when_missing(
        self,
    ) -> None:
        """Build available snap for missing parallel snap instance."""
        charm = self.harness.charm

        class SnapNotFoundError(Exception):
            pass

        charm.snap_module.SnapNotFoundError = SnapNotFoundError
        available_snap = MagicMock()
        charm.snap_module.Snap.return_value = available_snap
        cache = MagicMock()
        cache.__getitem__.side_effect = SnapNotFoundError
        cache._snap_client.get_snap_information.return_value = {
            "channel": "latest/stable",
            "revision": "123",
            "confinement": "strict",
        }
        charm.snap_module.SnapCache.return_value = cache

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_noha",
        ):
            result = sunbeam_charm.OSBaseOperatorCharmSnap.get_snap(charm)

        self.assertIs(result, available_snap)
        cache.__getitem__.assert_called_once_with("mysnap_noha")
        cache._snap_client.get_snap_information.assert_called_once_with(
            "mysnap"
        )
        charm.snap_module.Snap.assert_called_once_with(
            name="mysnap_noha",
            state=charm.snap_module.SnapState.Available,
            channel="latest/stable",
            revision="123",
            confinement="strict",
            apps=None,
        )
        self.assertNotIn("mysnap_noha", cache._snap_map)

    def test_get_snap_blocks_invalid_parallel_instance_key(self) -> None:
        """Block invalid parallel snap instance keys."""
        charm = self.harness.charm

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_BadKey",
        ):
            with self.assertRaises(
                sunbeam_charm.sunbeam_guard.BlockedExceptionError
            ):
                sunbeam_charm.OSBaseOperatorCharmSnap.get_snap(charm)

        charm.snap_module.SnapCache.assert_called_once_with()

    def test_ensure_snap_present_already_installed(self) -> None:
        """Test ensure_snap_present when snap is already correctly installed."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is already present with correct channel and confinement
        snap.present = True
        snap.channel = "latest/stable"
        snap.latest = False

        # Mock SnapClient to return snap as installed without devmode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        # No devmode requested (update config without triggering ensure_snap_present)
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        snap.reset_mock()
        charm.ensure_snap_present()

        # Snap should not be modified
        snap.ensure.assert_not_called()

    def test_ensure_snap_present_not_installed(self) -> None:
        """Test ensure_snap_present when snap is not installed."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is not present
        snap.present = False
        snap.channel = "latest/stable"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = []
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        snap.reset_mock()
        charm.ensure_snap_present()

        # Snap should be installed with Latest state
        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="latest/stable",
            devmode=False,
        )

    def test_ensure_snap_present_track_change_no_swap(self) -> None:
        """Test ensure_snap_present logs track mismatch instead of swapping."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present but on a different track
        snap.present = True
        snap.channel = "2024.1/stable"
        snap.latest = False

        # Mock SnapClient
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        snap.reset_mock()
        charm.ensure_snap_present()

        # Snap should NOT be updated — only a log, no swap
        snap.ensure.assert_not_called()

    def test_ensure_snap_present_same_track_channel_change(self) -> None:
        """Test ensure_snap_present refreshes snap on same-track channel change.

        A risk change within the same track (minor upgrade, e.g.
        stable -> candidate) must still swap the snap to the
        configured channel via config.
        """
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present on latest/stable, config wants
        # latest/candidate (same track, different risk)
        snap.present = True
        snap.channel = "latest/stable"
        snap.latest = False

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "latest/candidate"})

        snap.reset_mock()
        charm.ensure_snap_present()

        # Snap should be refreshed to the configured channel
        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="latest/candidate",
            devmode=False,
        )

    def test_ensure_snap_present_confinement_change_not_latest(self) -> None:
        """Test ensure_snap_present when confinement changes and snap is not latest."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present, not latest, devmode needs to change
        snap.present = True
        snap.channel = "latest/stable"
        snap.latest = False
        snap.revision = "123"

        # Mock SnapClient - snap installed in strict mode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "124"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        # Request devmode
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        snap.reset_mock()
        charm.ensure_snap_present()

        # Snap should be updated to Latest with devmode
        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="latest/stable",
            devmode=True,
        )

    def test_ensure_snap_present_confinement_change_is_latest(self) -> None:
        """Test ensure_snap_present blocks on latest confinement change."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present, is latest, devmode needs to change
        snap.present = True
        snap.channel = "latest/stable"
        snap.latest = True
        snap.revision = "456"
        snap.get.return_value = {"settings.foo": "bar"}

        # Mock SnapClient - snap installed in strict mode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "456"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        # Request devmode
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        snap.reset_mock()
        with self.assertLogs(sunbeam_charm.logger, level="ERROR") as logs:
            with self.assertRaises(
                sunbeam_charm.sunbeam_guard.BlockedExceptionError
            ) as exc:
                charm.ensure_snap_present()

        self.assertEqual(
            exc.exception.to_status(),
            ops.model.BlockedStatus("Invalid snap state: see juju debug-logs"),
        )
        self.assertIn("Invalid snap state", "\n".join(logs.output))
        self.assertIn("mysnap", "\n".join(logs.output))
        self.assertIn("revision 456", "\n".join(logs.output))
        self.assertIn("latest/stable", "\n".join(logs.output))

        snap.ensure.assert_not_called()
        snap.get.assert_not_called()
        snap.set.assert_not_called()

    def test_ensure_snap_present_confinement_and_channel_change_same_revision(
        self,
    ) -> None:
        """Test ensure_snap_present blocks on same-revision confinement change."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present, is latest, both channel and devmode need to
        # change but the requested channel resolves to the installed revision.
        snap.present = True
        snap.channel = "2024.1/beta"
        snap.latest = True
        snap.revision = "789"
        snap.get.return_value = {"settings.debug": True}

        # Mock SnapClient - snap installed in strict mode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "789"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        # Request devmode
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        snap.reset_mock()
        with self.assertLogs(sunbeam_charm.logger, level="ERROR") as logs:
            with self.assertRaises(
                sunbeam_charm.sunbeam_guard.BlockedExceptionError
            ) as exc:
                charm.ensure_snap_present()

        self.assertEqual(
            exc.exception.to_status(),
            ops.model.BlockedStatus("Invalid snap state: see juju debug-logs"),
        )
        self.assertIn("Invalid snap state", "\n".join(logs.output))
        self.assertIn("mysnap", "\n".join(logs.output))
        self.assertIn("revision 789", "\n".join(logs.output))
        self.assertIn("2024.1/beta", "\n".join(logs.output))

        snap.ensure.assert_not_called()
        snap.get.assert_not_called()
        snap.set.assert_not_called()

    def test_ensure_parallel_snap_confinement_change_same_revision_blocks(
        self,
    ) -> None:
        """Block parallel snap confinement change without revision change."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2024.1/stable"
        snap.latest = True
        snap.revision = "116"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap_noha", "devmode": True}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "116"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_noha",
        ):
            snap.reset_mock()
            with self.assertLogs(sunbeam_charm.logger, level="ERROR") as logs:
                with self.assertRaises(
                    sunbeam_charm.sunbeam_guard.BlockedExceptionError
                ) as exc:
                    charm.ensure_snap_present()

        self.assertEqual(
            exc.exception.to_status(),
            ops.model.BlockedStatus("Invalid snap state: see juju debug-logs"),
        )
        self.assertIn("mysnap_noha", "\n".join(logs.output))
        self.assertIn("revision 116", "\n".join(logs.output))
        snap.ensure.assert_not_called()

    def test_ensure_parallel_snap_track_change_no_swap(
        self,
    ) -> None:
        """Track change on parallel snap instance does not swap from hook."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2024.1/stable"
        snap.latest = True
        snap.revision = "116"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap_noha", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "116"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_noha",
        ):
            snap.reset_mock()
            charm.ensure_snap_present()

        snap.ensure.assert_not_called()

    def test_ensure_snap_present_track_mismatch_logs_only(self) -> None:
        """Test track mismatch logs instead of swapping."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2024.1/stable"
        snap.latest = True
        snap.revision = "116"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap_noha", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "116"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": False})

        with patch.object(
            type(charm),
            "snap_name",
            new_callable=PropertyMock,
            return_value="mysnap_noha",
        ):
            snap.reset_mock()
            charm.ensure_snap_present()

        snap.ensure.assert_not_called()

    def test_ensure_snap_present_confinement_and_channel_change_new_revision(
        self,
    ) -> None:
        """Test ensure_snap_present refreshes on new-revision confinement change."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present, is latest, devmode needs to change and the
        # installed channel is on a different track to the configured one, so
        # the confinement change must keep the installed channel.
        snap.present = True
        snap.channel = "2024.1/beta"
        snap.latest = True
        snap.revision = "789"

        # Mock SnapClient - snap installed in strict mode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"2024.1/beta": {"revision": "790"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        # Request devmode
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2024.1/beta",
            devmode=True,
        )

    def test_ensure_snap_present_confinement_change_bare_risk_resolves_track(
        self,
    ) -> None:
        """Confinement change with bare risk config resolves against installed track."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/beta"
        snap.latest = True
        snap.revision = "789"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"2026.1/stable": {"revision": "790"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config(
                {"snap-channel": "stable", "experimental-devmode": True}
            )

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2026.1/stable",
            devmode=True,
        )

    def test_ensure_snap_present_confinement_change_missing_target_revision(
        self,
    ) -> None:
        """Test ensure_snap_present blocks when target revision is unknown."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        # Setup: snap is present, devmode needs to change, but snapd does not
        # provide the requested channel revision.
        snap.present = True
        snap.channel = "2024.1/beta"
        snap.latest = True
        snap.revision = "789"

        # Mock SnapClient - snap installed in strict mode
        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        snap_client.get_snap_information.return_value = {
            "channels": {"latest/stable": {"revision": "789"}}
        }
        charm.snap_module.SnapClient.return_value = snap_client

        # Request devmode
        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        snap.reset_mock()
        with self.assertLogs(sunbeam_charm.logger, level="ERROR") as logs:
            with self.assertRaises(
                sunbeam_charm.sunbeam_guard.BlockedExceptionError
            ) as exc:
                charm.ensure_snap_present()

        self.assertEqual(
            exc.exception.to_status(),
            ops.model.BlockedStatus("Invalid snap state: see juju debug-logs"),
        )
        self.assertIn("target revision", "\n".join(logs.output))

        snap.ensure.assert_not_called()

    def test_refresh_snap_action_preserves_configured_devmode(self) -> None:
        """Test refresh-snap action uses configured devmode confinement."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"experimental-devmode": True})

        charm._on_refresh_snap_action(self.mock_event)

        snap.unhold.assert_called_once_with()
        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="latest/stable",
            devmode=True,
        )
        snap.hold.assert_called_once_with()

    def test_refresh_snap_action_channel_param_overrides_config(self) -> None:
        """Test refresh-snap action uses the optional channel param."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        event = MagicMock()
        event.params = {"channel": "2025.1/stable"}

        charm._on_refresh_snap_action(event)

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2025.1/stable",
            devmode=False,
        )

    def test_refresh_snap_action_bare_risk_resolves_installed_track(
        self,
    ) -> None:
        """refresh-snap --channel edge resolves against installed track."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/stable"

        event = MagicMock()
        event.params = {"channel": "edge"}

        charm._on_refresh_snap_action(event)

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2026.1/edge",
            devmode=False,
        )

    def test_snap_channel_status_drift(self) -> None:
        """Test channel drift is published as an active status message."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2024.1/stable"

        charm._update_snap_channel_status()

        self.assertEqual(charm.snap_channel_status.status.name, "active")
        self.assertEqual(
            charm.snap_channel_status.message(),
            "installed snap: 2024.1/stable, configured: latest/stable",
        )

    def test_snap_channel_status_aligned(self) -> None:
        """Test aligned channels leave the status non-competing."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "latest/stable"

        charm._update_snap_channel_status()

        # UnknownStatus never outranks a real status in the pool.
        self.assertEqual(charm.snap_channel_status.status.name, "unknown")
        self.assertEqual(charm.snap_channel_status.message(), "")

    def test_snap_channel_status_cleared_when_absent(self) -> None:
        """Test a persisted drift message is cleared when the snap is gone."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2024.1/stable"
        charm._update_snap_channel_status()
        self.assertNotEqual(charm.snap_channel_status.message(), "")

        snap.present = False
        charm._update_snap_channel_status()
        self.assertEqual(charm.snap_channel_status.status.name, "unknown")
        self.assertEqual(charm.snap_channel_status.message(), "")

    def test_snap_channel_status_shorthand_equivalent(self) -> None:
        """Bare risk names are equivalent to latest/<risk> channels."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "latest/stable"

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "stable"})

        charm._update_snap_channel_status()

        self.assertEqual(charm.snap_channel_status.status.name, "unknown")
        self.assertEqual(charm.snap_channel_status.message(), "")

    def test_snap_channel_status_resolved_bare_risk(self) -> None:
        """Bare risk config resolves against installed track in status."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/stable"

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "candidate"})

        charm._update_snap_channel_status()

        self.assertEqual(charm.snap_channel_status.status.name, "active")
        self.assertEqual(
            charm.snap_channel_status.message(),
            "installed snap: 2026.1/stable, configured: 2026.1/candidate",
        )

    def test_ensure_snap_present_shorthand_risk_change(self) -> None:
        """Bare risk names resolve against the installed snap's track."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "edge"
        snap.latest = False

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "beta"})

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="latest/beta",
            devmode=False,
        )

    def test_canonical_snap_channel_risk_branch(self) -> None:
        """risk/branch shorthand (edge/hotfix) -> latest/edge/hotfix."""
        self.assertEqual(
            sunbeam_charm.canonical_snap_channel("edge/hotfix"),
            "latest/edge/hotfix",
        )
        self.assertEqual(
            sunbeam_charm.canonical_snap_channel("stable/fix"),
            "latest/stable/fix",
        )

    def test_resolve_snap_channel_bare_risk(self) -> None:
        """Bare risk resolves to the installed track, not latest."""
        self.assertEqual(
            sunbeam_charm.resolve_snap_channel("2026.1/stable", "candidate"),
            "2026.1/candidate",
        )
        self.assertEqual(
            sunbeam_charm.resolve_snap_channel("latest/stable", "candidate"),
            "latest/candidate",
        )

    def test_resolve_snap_channel_risk_branch(self) -> None:
        """risk/branch shorthand resolves to the installed track."""
        self.assertEqual(
            sunbeam_charm.resolve_snap_channel("2026.1/stable", "edge/hotfix"),
            "2026.1/edge/hotfix",
        )

    def test_resolve_snap_channel_full_channel(self) -> None:
        """Fully-specified channel is returned unchanged."""
        self.assertEqual(
            sunbeam_charm.resolve_snap_channel(
                "2026.1/stable", "2025.2/candidate"
            ),
            "2025.2/candidate",
        )

    def test_snap_channel_drift_message_resolved(self) -> None:
        """Bare risk resolves against installed track in drift message."""
        self.assertEqual(
            sunbeam_charm.snap_channel_drift_message(
                "2026.1/stable", "candidate"
            ),
            "installed snap: 2026.1/stable, configured: 2026.1/candidate",
        )
        self.assertEqual(
            sunbeam_charm.snap_channel_drift_message(
                "2026.1/stable", "2025.2/candidate"
            ),
            "installed snap: 2026.1/stable, configured: 2025.2/candidate",
        )

    def test_ensure_snap_present_bare_risk_resolves_track(self) -> None:
        """Installed 2026.1/stable + configured candidate refreshes to 2026.1/candidate."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/stable"
        snap.latest = False

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "candidate"})

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2026.1/candidate",
            devmode=False,
        )

    def test_ensure_snap_present_risk_branch_resolves_track(self) -> None:
        """Installed 2026.1/stable + configured edge/hotfix refreshes to 2026.1/edge/hotfix."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/stable"
        snap.latest = False

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "edge/hotfix"})

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_called_once_with(
            charm.snap_module.SnapState.Latest,
            channel="2026.1/edge/hotfix",
            devmode=False,
        )

    def test_ensure_snap_present_different_track_still_skips(self) -> None:
        """Installed 2026.1/stable + configured 2025.2/candidate still skipped."""
        charm = self.harness.charm
        snap = charm.mock_snap
        snap.reset_mock()

        snap.present = True
        snap.channel = "2026.1/stable"
        snap.latest = True
        snap.revision = "116"

        snap_client = MagicMock()
        snap_client.get_installed_snaps.return_value = [
            {"name": "mysnap", "devmode": False}
        ]
        charm.snap_module.SnapClient.return_value = snap_client

        with patch.object(charm, "ensure_snap_present"):
            self.harness.update_config({"snap-channel": "2025.2/candidate"})

        snap.reset_mock()
        charm.ensure_snap_present()

        snap.ensure.assert_not_called()


class TestOSBaseOperatorAPICharmActions(_TestOSBaseOperatorAPICharm):
    """Test pause and resume actions for API charms."""

    def setUp(self) -> None:
        """Run test class setup."""
        super().setUp(test_charms.MyAPICharm)

    def _setup_charm_ready(self) -> None:
        """Set up charm to be in a ready state."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        test_utils.add_complete_peer_relation(self.harness)
        self.set_pebble_ready()
        self.harness.charm.leader_set({"foo": "bar"})
        test_utils.add_api_relations(self.harness)
        test_utils.add_complete_identity_credentials_relation(self.harness)
        self.harness.set_can_connect("my-service", True)

    def test_pause_action_stops_healthcheck(self) -> None:
        """Test that pause action stops healthcheck for all pebble handlers."""
        self._setup_charm_ready()

        for ph in self.harness.charm.pebble_handlers:
            ph.stop_healthcheck = MagicMock()

        result = self.harness.run_action("pause")

        for ph in self.harness.charm.pebble_handlers:
            ph.stop_healthcheck.assert_called_once_with("up")
        self.assertEqual(result.results, {"ok": True})

    def test_pause_enter_maintenance(self) -> None:
        """Test that pause action sets maintenance status."""
        self._setup_charm_ready()

        self.harness.run_action("pause")

        self.harness.charm.on.update_status.emit()
        self.assertIsInstance(
            self.harness.charm.status.status, ops.model.MaintenanceStatus
        )

    def test_pause_action_stops_services(self) -> None:
        """Test that pause action stops all services."""
        self._setup_charm_ready()

        with patch.object(self.harness.charm, "stop_services") as mock_stop:
            self.harness.run_action("pause")

            mock_stop.assert_called()

    def test_pause_action_fails_when_peer_state_cannot_be_set(self) -> None:
        """Test that pause fails when peer state cannot be persisted."""
        self._setup_charm_ready()

        with patch.object(
            self.harness.charm.peers,
            "set_unit_paused_state",
            side_effect=RuntimeError("failed to set peer state"),
        ), self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("pause")

    def test_resume_action_starts_healthcheck(self) -> None:
        """Test that resume action starts healthcheck for all pebble handlers."""
        self._setup_charm_ready()

        for ph in self.harness.charm.pebble_handlers:
            ph.start_healthcheck = MagicMock()

        result = self.harness.run_action("resume")

        for ph in self.harness.charm.pebble_handlers:
            ph.start_healthcheck.assert_called_once_with("up")
        self.assertEqual(result.results, {"ok": True})

    def test_resume_clears_local_state_before_configuring(self) -> None:
        """Test that resume clears local paused state before configuration."""
        charm = self.harness.charm
        charm._state.paused = True

        with patch.object(
            type(charm), "supports_peer_relation", new_callable=PropertyMock
        ) as mock_supports_peer, patch.object(
            charm, "configure_charm"
        ) as mock_configure:
            mock_supports_peer.return_value = False

            def configure_charm(event):
                self.assertFalse(charm.is_service_paused)
                charm._configure_charm_completed = True

            mock_configure.side_effect = configure_charm

            charm._on_resume_action(self.mock_event)

        mock_configure.assert_called_once_with(self.mock_event)

    def test_resume_action_fails_when_configure_charm_does_not_complete(
        self,
    ) -> None:
        """Test that resume fails when configure_charm does not complete."""
        self._setup_charm_ready()

        with patch.object(
            self.harness.charm, "configure_charm"
        ) as mock_configure:
            mock_configure.side_effect = lambda event: setattr(
                self.harness.charm, "_configure_charm_completed", False
            )

            with self.assertRaises(ops.testing.ActionFailed):
                self.harness.run_action("resume")

    def test_resume_action_fails_when_peer_state_cannot_be_cleared(
        self,
    ) -> None:
        """Test that resume fails when peer state cannot be persisted."""
        self._setup_charm_ready()

        with patch.object(
            self.harness.charm.peers,
            "set_unit_paused_state",
            side_effect=RuntimeError("failed to clear peer state"),
        ), self.assertRaises(ops.testing.ActionFailed):
            self.harness.run_action("resume")

    def test_healthcheck_helpers_use_requested_check_name(self) -> None:
        """Test healthcheck helpers use the requested check name."""
        ph = self.harness.charm.pebble_handlers[0]
        container = MagicMock()
        container.get_plan.return_value.checks = {"ready": {}}

        with patch.object(
            ph.charm.unit, "get_container", return_value=container
        ):
            ph.stop_healthcheck("ready")
            ph.start_healthcheck("ready")

        container.stop_checks.assert_called_once_with("ready")
        container.start_checks.assert_called_once_with("ready")

    def test_resume_action_initializes_container_services(self) -> None:
        """Test that resume action initializes container services."""
        self._setup_charm_ready()

        with patch.object(
            self.harness.charm, "init_container_services"
        ) as mock_init:
            self.harness.run_action("resume")

            mock_init.assert_called_once()
