# Copyright 2024 Canonical Ltd.
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
"""Unit tests for tempest-related resources cleanup."""

import textwrap
import unittest
from unittest.mock import (
    MagicMock,
    call,
    mock_open,
    patch,
)

from keystoneauth1.exceptions.catalog import (
    EndpointNotFound,
)
from keystoneauth1.exceptions.http import (
    Unauthorized,
)
from openstack.exceptions import (
    ForbiddenException,
    ResourceNotFound,
    ServiceDisabledException,
)
from utils.cleanup import (
    CleanUpError,
    _cleanup_block_resources,
    _cleanup_compute_resources,
    _cleanup_images,
    _cleanup_leaked_networks,
    _cleanup_network_ports,
    _cleanup_networks_resources,
    _cleanup_project,
    _cleanup_stacks,
    _cleanup_users,
    _connect_to_os,
    _get_exclusion_resources,
    _get_test_projects_in_domain,
    _run_cleanup_functions,
    run_extensive_cleanup,
    run_quick_cleanup,
)


class TestCleanup(unittest.TestCase):
    """Test tempest resources clean-up."""

    def setUp(self):
        """Set up test construction."""
        self.patcher = patch("utils.cleanup.Connection")
        self.mock_connection = self.patcher.start()

    def tearDown(self):
        """Tear down test construction."""
        self.patcher.stop()

    def test_connect_to_os(self):
        """Test establishing OS connection."""
        env = {
            "OS_CACERT": "/usr/local/share/ca-certificates/ca-bundle.pem",
            "OS_AUTH_URL": "http://10.6.0.20/openstack-keystone",
            "OS_USERNAME": "test_user",
            "OS_PASSWORD": "userpass",
            "OS_PROJECT_NAME": "test_project",
            "OS_DOMAIN_ID": "domain_id",
            "OS_USER_DOMAIN_ID": "domain_id",
            "OS_PROJECT_DOMAIN_ID": "domain_id",
        }
        _connect_to_os(env)

        self.mock_connection.assert_called_once_with(
            auth_url=env["OS_AUTH_URL"],
            project_name=env["OS_PROJECT_NAME"],
            username=env["OS_USERNAME"],
            password=env["OS_PASSWORD"],
            user_domain_id=env["OS_USER_DOMAIN_ID"],
            project_domain_id=env["OS_USER_DOMAIN_ID"],
            cacert=env["OS_CACERT"],
        )

    def test_get_exclude_resources(self):
        """Test get exclude resources from test accounts file."""
        account_file_content = textwrap.dedent("""
        - domain_name: mydomain
          password: password1
          project_name: tempest-test_creds-11949114
          resources:
            network: tempest-test_creds-9369097-network
          username: tempest-test_creds-11949114-project
        - domain_name: mydomain
          password: password2
          project_name: tempest-test_creds-18083146
          resources:
            network: tempest-test_creds-20041716-network
          username: tempest-test_creds-18083146-project
        """)

        expected_result = {
            "projects": {
                "tempest-test_creds-11949114",
                "tempest-test_creds-18083146",
            },
            "users": {
                "tempest-test_creds-11949114-project",
                "tempest-test_creds-18083146-project",
            },
            "networks": {
                "tempest-test_creds-9369097-network",
                "tempest-test_creds-20041716-network",
            },
        }

        with patch(
            "utils.cleanup.open",
            new_callable=mock_open,
            read_data=account_file_content,
        ):
            result = _get_exclusion_resources("test_account_file.yaml")

        self.assertEqual(result, expected_result)

    def test_get_exclude_resources_system_accounts(self):
        """Test system-scoped accounts, which have no project, are handled."""
        account_file_content = textwrap.dedent("""
        - domain_name: mydomain
          password: password1
          project_name: tempest-test_creds-11949114
          resources:
            network: tempest-test_creds-9369097-network
          roles:
          - member
          username: tempest-test_creds-11949114-project-member
        - password: password2
          roles:
          - reader
          system: all
          username: tempest-test_creds-18083146-system-reader
        """)

        with patch(
            "utils.cleanup.open",
            new_callable=mock_open,
            read_data=account_file_content,
        ):
            result = _get_exclusion_resources("test_account_file.yaml")

        self.assertEqual(
            result,
            {
                "projects": {"tempest-test_creds-11949114"},
                "users": {
                    "tempest-test_creds-11949114-project-member",
                    "tempest-test_creds-18083146-system-reader",
                },
                "networks": {"tempest-test_creds-9369097-network"},
            },
        )

    def test_get_exclude_resources_missing_username(self):
        """Test an account without a username is a malformed file."""
        with patch(
            "utils.cleanup.open",
            new_callable=mock_open,
            read_data="- project_name: tempest-test_creds-1\n",
        ):
            with self.assertRaises(CleanUpError) as error:
                _get_exclusion_resources("test_account_file.yaml")

        self.assertEqual(
            str(error.exception), "Malformated test account file."
        )

    def test_get_test_projects_in_domain(self):
        """Test get tempest projects of a specified domain."""
        tempest_project1 = MagicMock()
        tempest_project1.configure_mock(id="1", name="tempest-project-1")
        tempest_project2 = MagicMock()
        tempest_project2.configure_mock(id="2", name="tempest-project-2")
        non_tempest_project = MagicMock()
        non_tempest_project.configure_mock(id="3", name="non-tempest-project")
        self.mock_connection.identity.projects.return_value = [
            tempest_project1,
            tempest_project2,
            non_tempest_project,
        ]

        projects = _get_test_projects_in_domain(
            self.mock_connection, "tempest_domain"
        )

        self.assertEqual(projects, ["1", "2"])

    def test_get_test_projects_in_domain_with_exclusion(self):
        """Test get tempest projects of a specified domain with exclusion."""
        tempest_project1 = MagicMock()
        tempest_project1.configure_mock(id="1", name="tempest-project-1")
        tempest_project2 = MagicMock()
        tempest_project2.configure_mock(id="2", name="tempest-project-2")
        non_tempest_project = MagicMock()
        non_tempest_project.configure_mock(id="3", name="non-tempest-project")
        self.mock_connection.identity.projects.return_value = [
            tempest_project1,
            tempest_project2,
            non_tempest_project,
        ]

        projects = _get_test_projects_in_domain(
            self.mock_connection,
            domain_id="tempest_domain",
            exclude_projects={"tempest-project-1"},
        )

        self.assertEqual(projects, ["2"])

    def test_cleanup_block_resources(self):
        """Test cleanup volumes and snapshots."""
        tempest_snapshots = MagicMock()
        tempest_snapshots.configure_mock(id="1", name="tempest-snapshots-1")
        non_tempest_snapshots = MagicMock()
        non_tempest_snapshots.configure_mock(
            id="2", name="non-tempest-snapshots"
        )
        self.mock_connection.block_store.snapshots.return_value = [
            tempest_snapshots,
            non_tempest_snapshots,
        ]

        tempest_volume = MagicMock()
        tempest_volume.configure_mock(id="1", name="tempest-volume-1")
        non_tempest_volume = MagicMock()
        non_tempest_volume.configure_mock(id="2", name="non-tempest-volume")
        self.mock_connection.block_store.volumes.return_value = [
            tempest_volume,
            non_tempest_volume,
        ]

        _cleanup_block_resources(self.mock_connection, "test_project")

        self.mock_connection.block_store.delete_snapshot.assert_called_once_with(
            "1"
        )
        self.mock_connection.block_store.delete_volume.assert_called_once_with(
            "1"
        )

    def test_cleanup_networks_resources(self):
        """Test cleanup network resources."""
        tempest_router = MagicMock()
        tempest_router.configure_mock(id="1", name="tempest-router")
        non_tempest_router = MagicMock()
        non_tempest_router.configure_mock(id="2", name="non-tempest-router")
        self.mock_connection.network.routers.return_value = [
            tempest_router,
            non_tempest_router,
        ]

        tempest_port1 = MagicMock()
        tempest_port1.configure_mock(id="1")
        tempest_port2 = MagicMock()
        tempest_port2.configure_mock(id="2")
        self.mock_connection.network.ports.return_value = [
            tempest_port1,
            tempest_port2,
        ]

        tempest_network = MagicMock()
        tempest_network.configure_mock(id="1", name="tempest-network")
        non_tempest_network = MagicMock()
        non_tempest_network.configure_mock(id="2", name="non-tempest-network")
        self.mock_connection.network.networks.return_value = [
            tempest_network,
            non_tempest_network,
        ]

        _cleanup_networks_resources(self.mock_connection, "project_id")

        self.mock_connection.network.remove_interface_from_router.call_args_list == [
            call(tempest_router, port_id="1"),
            call(tempest_router, port_id="2"),
        ]
        self.mock_connection.network.delete_router.assert_called_once_with("1")
        self.mock_connection.network.delete_network.assert_called_once_with(
            "1"
        )

    def _port(self, port_id, owner, device_id=""):
        port = MagicMock()
        port.configure_mock(
            id=port_id, device_owner=owner, device_id=device_id
        )
        return port

    def test_cleanup_network_ports(self):
        """Ports are released according to their owner."""
        self.mock_connection.network.ports.return_value = [
            self._port("vm", "compute:nova"),
            self._port("vip", "Octavia"),
            self._port("ri", "network:router_interface", "router-1"),
            self._port("dhcp", "network:dhcp"),
        ]

        _cleanup_network_ports(self.mock_connection, "net-1")

        self.mock_connection.network.ports.assert_called_once_with(
            network_id="net-1"
        )
        self.assertEqual(
            self.mock_connection.network.delete_port.call_args_list,
            [
                call("vm", ignore_missing=True),
                call("vip", ignore_missing=True),
            ],
        )
        self.mock_connection.network.remove_interface_from_router.assert_called_once_with(
            "router-1", port_id="ri"
        )

    def test_cleanup_networks_resources_deletes_ports_first(self):
        """Ports on a network are deleted before the network itself."""
        self.mock_connection.network.routers.return_value = []
        network = MagicMock()
        network.configure_mock(id="1", name="tempest-network")
        self.mock_connection.network.networks.return_value = [network]
        self.mock_connection.network.ports.return_value = [
            self._port("vm", "compute:nova")
        ]
        order = MagicMock()
        order.attach_mock(self.mock_connection.network.delete_port, "port")
        order.attach_mock(self.mock_connection.network.delete_network, "net")

        _cleanup_networks_resources(self.mock_connection, "project_id")

        self.assertEqual([c[0] for c in order.mock_calls], ["port", "net"])

    def test_cleanup_networks_resources_reports_failed_networks(self):
        """A failing network is reported and does not stop the others."""
        self.mock_connection.network.routers.return_value = []
        bad = MagicMock()
        bad.configure_mock(id="1", name="tempest-bad")
        good = MagicMock()
        good.configure_mock(id="2", name="tempest-good")
        self.mock_connection.network.networks.return_value = [bad, good]
        self.mock_connection.network.ports.return_value = []
        self.mock_connection.network.delete_network.side_effect = [
            Exception("Conflict"),
            None,
        ]

        with self.assertRaises(CleanUpError) as error:
            _cleanup_networks_resources(self.mock_connection, "project_id")

        self.assertIn("tempest-bad (1): Conflict", str(error.exception))
        self.assertNotIn("tempest-good", str(error.exception))
        self.assertEqual(
            self.mock_connection.network.delete_network.call_count, 2
        )

    def test_cleanup_networks_resources_network_already_gone(self):
        """A network deleted in the meantime is not an error."""
        self.mock_connection.network.routers.return_value = []
        network = MagicMock()
        network.configure_mock(id="1", name="tempest-network")
        self.mock_connection.network.networks.return_value = [network]
        self.mock_connection.network.ports.return_value = []
        self.mock_connection.network.delete_network.side_effect = (
            ResourceNotFound
        )

        _cleanup_networks_resources(self.mock_connection, "project_id")

    def test_cleanup_leaked_networks_keeps_account_networks(self):
        """Pre-provisioned account networks are kept, leaked ones removed."""
        base = MagicMock()
        base.configure_mock(id="1", name="tempest-acct-1-network")
        leaked = MagicMock()
        leaked.configure_mock(id="2", name="tempest-test-net-2")
        foreign = MagicMock()
        foreign.configure_mock(id="3", name="private-net")
        self.mock_connection.network.networks.return_value = [
            base,
            leaked,
            foreign,
        ]
        self.mock_connection.network.ports.return_value = []

        _cleanup_leaked_networks(
            self.mock_connection, "project_id", {"tempest-acct-1-network"}
        )

        self.mock_connection.network.delete_network.assert_called_once_with(
            "2"
        )
        self.mock_connection.network.delete_router.assert_not_called()

    def test_cleanup_compute_resources(self):
        """Test cleanup instances and keypairs."""
        tempest_instance = MagicMock()
        tempest_instance.configure_mock(id="1", name="tempest-server-1")
        non_tempest_instance = MagicMock()
        non_tempest_instance.configure_mock(id="2", name="non-tempest-server")
        self.mock_connection.compute.servers.return_value = [
            tempest_instance,
            non_tempest_instance,
        ]

        tempest_keypair = MagicMock()
        tempest_keypair.configure_mock(id="1", name="tempest-keypair-1")
        non_tempest_keypair = MagicMock()
        non_tempest_keypair.configure_mock(
            id="2", name="non-tempest-keypair-2"
        )

        self.mock_connection.compute.keypairs.return_value = [
            tempest_keypair,
            non_tempest_keypair,
        ]

        _cleanup_compute_resources(self.mock_connection, "test_project")

        self.mock_connection.compute.delete_server.assert_called_once_with("1")
        self.mock_connection.compute.delete_keypair.assert_called_once_with(
            tempest_keypair
        )
        self.mock_connection.compute.wait_for_delete.assert_called_once_with(
            tempest_instance, wait=300
        )

    def test_cleanup_images(self):
        """Test cleanup images."""
        image_owned_by_project = MagicMock()
        image_owned_by_project.configure_mock(
            id="1", name="image-1", owner="test_project"
        )
        image_not_owned_by_project = MagicMock()
        image_not_owned_by_project.configure_mock(
            id="3", name="image-3", owner="not_test_project"
        )
        self.mock_connection.image.images.return_value = [
            image_owned_by_project,
            image_not_owned_by_project,
        ]

        _cleanup_images(self.mock_connection, "test_project")

        self.mock_connection.image.delete_image.assert_called_once_with("1")

    def test_cleanup_project(self):
        """Test cleanup projects."""
        project_id = "tempest_project_id"

        _cleanup_project(self.mock_connection, project_id)

        self.mock_connection.identity.delete_project.assert_called_with(
            project_id
        )

    def test_cleanup_users(self):
        """Test cleanup users."""
        tempest_user = MagicMock()
        tempest_user.configure_mock(
            domain_id="tempest", id="1", name="tempest-user-1"
        )
        non_tempest_user = MagicMock()
        non_tempest_user.configure_mock(
            domain_id="tempest", id="2", name="non-tempest-user-2"
        )

        self.mock_connection.identity.users.return_value = [
            tempest_user,
            non_tempest_user,
        ]

        _cleanup_users(self.mock_connection, domain_id="tempest")

        self.mock_connection.identity.delete_user.assert_called_once_with("1")

    def test_cleanup_users_with_exclusion(self):
        """Test cleanup users with exclusion."""
        tempest_user1 = MagicMock()
        tempest_user1.configure_mock(
            domain_id="tempest", id="1", name="tempest-user-1"
        )
        tempest_user2 = MagicMock()
        tempest_user2.configure_mock(
            domain_id="tempest", id="2", name="tempest-user-2"
        )
        non_tempest_user = MagicMock()
        non_tempest_user.configure_mock(
            domain_id="tempest", id="3", name="non-tempest-user"
        )

        self.mock_connection.identity.users.return_value = [
            tempest_user1,
            tempest_user2,
            non_tempest_user,
        ]

        _cleanup_users(
            self.mock_connection,
            domain_id="tempest",
            exclude_users={"tempest-user-1"},
        )

        self.mock_connection.identity.delete_user.assert_called_once_with("2")

    def test_cleanup_stacks_success(self):
        """Test cleanup heat stacks."""
        tempest_stack = MagicMock()
        tempest_stack.configure_mock(id="1", name="tempest-stack-1")
        non_tempest_stack = MagicMock()
        non_tempest_stack.configure_mock(id="2", name="non-tempest-stack-2")
        self.mock_connection.orchestration.stacks.return_value = [
            tempest_stack,
            non_tempest_stack,
        ]

        _cleanup_stacks(self.mock_connection, "test_project")

        self.mock_connection.orchestration.delete_stack.assert_called_once_with(
            "1"
        )

    def test_cleanup_stacks_endpoint_not_found(self):
        """Test cleanup stacks when heat endpoint is not found."""
        self.mock_connection.orchestration.stacks.side_effect = (
            EndpointNotFound
        )

        _cleanup_stacks(self.mock_connection, "test_project")

        self.mock_connection.orchestration.delete_stack.assert_not_called()

    def test_cleanup_stacks_service_disabled(self):
        """Test cleanup stacks when heat is not in the service catalog."""
        self.mock_connection.orchestration.stacks.side_effect = (
            ServiceDisabledException
        )

        _cleanup_stacks(self.mock_connection, "test_project")

        self.mock_connection.orchestration.delete_stack.assert_not_called()

    @patch("utils.cleanup._cleanup_compute_resources")
    @patch("utils.cleanup._cleanup_block_resources")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._cleanup_networks_resources")
    @patch("utils.cleanup._cleanup_project")
    def test_run_cleanup_functions(
        self,
        mock_cleanup_project,
        mock_cleanup_networks_resources,
        mock_cleanup_stacks,
        mock_cleanup_images,
        mock_cleanup_block_resources,
        mock_cleanup_compute_resources,
    ):
        """Test run cleanup functions."""
        cleanup_funcs = [
            mock_cleanup_compute_resources,
            mock_cleanup_block_resources,
            mock_cleanup_images,
            mock_cleanup_stacks,
            mock_cleanup_networks_resources,
            mock_cleanup_project,
        ]
        projects = ["tempest_project_id"]

        _run_cleanup_functions(self.mock_connection, projects, cleanup_funcs)

        mock_cleanup_stacks.assert_called_once()
        mock_cleanup_images.assert_called_once()
        mock_cleanup_block_resources.assert_called_once()
        mock_cleanup_compute_resources.assert_called_once()
        mock_cleanup_networks_resources.assert_called_once()
        mock_cleanup_project.assert_called_once()

    @patch("utils.cleanup._cleanup_compute_resources")
    @patch("utils.cleanup._cleanup_block_resources")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._cleanup_networks_resources")
    @patch("utils.cleanup._cleanup_project")
    def test_run_cleanup_functions_unsuccessful(
        self,
        mock_cleanup_project,
        mock_cleanup_networks_resources,
        mock_cleanup_stacks,
        mock_cleanup_images,
        mock_cleanup_block_resources,
        mock_cleanup_compute_resources,
    ):
        """Test run cleanup functions."""
        cleanup_funcs = [
            mock_cleanup_compute_resources,
            mock_cleanup_block_resources,
            mock_cleanup_images,
            mock_cleanup_stacks,
            mock_cleanup_networks_resources,
            mock_cleanup_project,
        ]
        projects = ["tempest_project_id"]
        mock_cleanup_images.__name__ = "_cleanup_images"
        mock_cleanup_images.side_effect = Exception

        _run_cleanup_functions(self.mock_connection, projects, cleanup_funcs)

        mock_cleanup_stacks.assert_called_once()
        mock_cleanup_images.assert_called_once()
        mock_cleanup_block_resources.assert_called_once()
        mock_cleanup_compute_resources.assert_called_once()
        mock_cleanup_networks_resources.assert_called_once()
        mock_cleanup_project.assert_called_once()

    @patch("utils.cleanup._run_cleanup_functions")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    @patch("utils.cleanup._get_exclusion_resources")
    def test_run_quick_cleanup(
        self,
        mock_get_exclusion_resources,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_run_cleanup_functions,
    ):
        """Test run quick cleanup."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = [
            ["tempest_project_id"],
            ["tempest_project_id"],
        ]

        run_quick_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_exclusion_resources.assert_called_once()
        mock_get_test_projects_in_domain.assert_called()
        mock_run_cleanup_functions.assert_called()
        mock_cleanup_users.assert_called_once()

    @patch("utils.cleanup._cleanup_leaked_networks")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    @patch("utils.cleanup._get_exclusion_resources")
    def test_run_quick_cleanup_leaked_networks_in_account_projects(
        self,
        mock_get_exclusion_resources,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_cleanup_leaked_networks,
    ):
        """Test quick cleanup removes leaked networks in account projects."""
        mock_get_exclusion_resources.return_value = {
            "projects": {"account-project"},
            "users": set(),
            "networks": {"account-network"},
        }
        # all test projects, then the ones not in the account file
        mock_get_test_projects_in_domain.side_effect = [
            ["account-id", "other-id"],
            ["other-id"],
        ]
        mock_cleanup_leaked_networks.side_effect = Exception("Conflict")

        with self.assertRaises(CleanUpError) as error:
            run_quick_cleanup(MagicMock())

        mock_cleanup_leaked_networks.assert_called_once_with(
            mock_connect_to_os.return_value,
            "account-id",
            {"account-network"},
        )
        self.assertIn(
            "_cleanup_account_project_networks", str(error.exception)
        )
        self.assertIn("Conflict", str(error.exception))

    @patch("utils.cleanup._run_cleanup_functions")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    @patch("utils.cleanup._get_exclusion_resources")
    def test_run_quick_cleanup_error(
        self,
        mock_get_exclusion_resources,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_run_cleanup_functions,
    ):
        """Test run quick cleanup with failure."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = ForbiddenException

        with self.assertRaises(CleanUpError) as error:
            run_quick_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_exclusion_resources.assert_called_once()
        mock_get_test_projects_in_domain.assert_called_once()
        self.assertEqual(str(error.exception), "Operation not authorized.")

        mock_run_cleanup_functions.assert_not_called()
        mock_cleanup_users.assert_not_called()

    @patch("utils.cleanup._run_cleanup_functions")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    @patch("utils.cleanup._get_exclusion_resources")
    def test_run_extensive_cleanup(
        self,
        mock_get_exclusion_resources,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_run_cleanup_functions,
    ):
        """Test run extensive cleanup."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = [
            ["tempest_project_id"],
            ["tempest_project_id"],
        ]

        run_extensive_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called()
        mock_run_cleanup_functions.assert_called_once()
        mock_cleanup_users.assert_called_once()

    @patch("utils.cleanup._run_cleanup_functions")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    @patch("utils.cleanup._get_exclusion_resources")
    def test_run_extensive_cleanup_permission_error(
        self,
        mock_get_exclusion_resources,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_run_cleanup_functions,
    ):
        """Test run extensive cleanup with failure."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = Unauthorized

        with self.assertRaises(CleanUpError) as error:
            run_extensive_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called_once()
        self.assertEqual(str(error.exception), "Operation not authorized.")

        mock_run_cleanup_functions.assert_not_called()
        mock_cleanup_users.assert_not_called()
