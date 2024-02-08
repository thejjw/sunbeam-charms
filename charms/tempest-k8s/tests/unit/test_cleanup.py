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
import unittest
from unittest.mock import (
    MagicMock,
    call,
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
)
from utils.cleanup import (
    CleanUpError,
    _cleanup_images,
    _cleanup_instances,
    _cleanup_keypairs,
    _cleanup_networks_resources,
    _cleanup_projects,
    _cleanup_stacks,
    _cleanup_users,
    _cleanup_volumes_and_snapshots,
    _connect_to_os,
    _get_test_projects_in_domain,
    run_extensive_cleanup,
    run_quick_cleanup,
)


class TestCleanup(unittest.TestCase):
    """Test tempest resources clean-up."""

    @patch("openstack.connection.Connection")
    def test_connect_to_os(self, mock_connection):
        """Test establishing OS connection."""
        env = {
            "OS_AUTH_URL": "http://10.6.0.20/openstack-keystone",
            "OS_USERNAME": "test_user",
            "OS_PASSWORD": "userpass",
            "OS_PROJECT_NAME": "test_project",
            "OS_DOMAIN_ID": "domain_id",
            "OS_USER_DOMAIN_ID": "domain_id",
            "OS_PROJECT_DOMAIN_ID": "domain_id",
        }
        _connect_to_os(env)

        mock_connection.assert_called_once_with(
            auth_url=env["OS_AUTH_URL"],
            project_name=env["OS_PROJECT_NAME"],
            username=env["OS_USERNAME"],
            password=env["OS_PASSWORD"],
            user_domain_id=env["OS_USER_DOMAIN_ID"],
            project_domain_id=env["OS_USER_DOMAIN_ID"],
        )

    @patch("openstack.connection.Connection")
    def test_get_test_projects_in_domain(self, mock_connection):
        """Test get tempest projects of a specified domain."""
        tempest_project1 = MagicMock()
        tempest_project1.configure_mock(id="1", name="tempest-project-1")
        tempest_project2 = MagicMock()
        tempest_project2.configure_mock(id="2", name="tempest-project-2")
        non_tempest_project = MagicMock()
        non_tempest_project.configure_mock(id="3", name="non-tempest-project")
        mock_connection.identity.projects.return_value = [
            tempest_project1,
            tempest_project2,
            non_tempest_project,
        ]

        projects = _get_test_projects_in_domain(
            mock_connection, "tempest_domain"
        )

        self.assertEqual(projects, ["1", "2"])

    @patch("openstack.connection.Connection")
    def test_cleanup_volumes_and_snapshots(self, mock_connection):
        """Test cleanup volumes and snapshots."""
        tempest_snapshots = MagicMock()
        tempest_snapshots.configure_mock(id="1", name="tempest-snapshots-1")
        non_tempest_snapshots = MagicMock()
        non_tempest_snapshots.configure_mock(
            id="2", name="non-tempest-snapshots"
        )
        mock_connection.block_store.snapshots.return_value = [
            tempest_snapshots,
            non_tempest_snapshots,
        ]

        tempest_volume = MagicMock()
        tempest_volume.configure_mock(id="1", name="tempest-volume-1")
        non_tempest_volume = MagicMock()
        non_tempest_volume.configure_mock(id="2", name="non-tempest-volume")
        mock_connection.block_store.volumes.return_value = [
            tempest_volume,
            non_tempest_volume,
        ]

        _cleanup_volumes_and_snapshots(mock_connection, "test_project")

        mock_connection.block_store.delete_snapshot.assert_called_once_with(
            "1"
        )
        mock_connection.block_store.delete_volume.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_networks_resources(self, mock_connection):
        """Test cleanup network resources."""
        tempest_router = MagicMock()
        tempest_router.configure_mock(id="1", name="tempest-router")
        non_tempest_router = MagicMock()
        non_tempest_router.configure_mock(id="2", name="non-tempest-router")
        mock_connection.network.routers.return_value = [
            tempest_router,
            non_tempest_router,
        ]

        tempest_port1 = MagicMock()
        tempest_port1.configure_mock(id="1")
        tempest_port2 = MagicMock()
        tempest_port2.configure_mock(id="2")
        mock_connection.network.ports.return_value = [
            tempest_port1,
            tempest_port2,
        ]

        tempest_network = MagicMock()
        tempest_network.configure_mock(id="1", name="tempest-network")
        non_tempest_network = MagicMock()
        non_tempest_network.configure_mock(id="2", name="non-tempest-network")
        mock_connection.network.networks.return_value = [
            tempest_network,
            non_tempest_network,
        ]

        tempest_ip = MagicMock()
        tempest_ip.configure_mock(id="1", description="tempest-ip")
        non_tempest_ip = MagicMock()
        non_tempest_ip.configure_mock(id="2", description="non-tempest-ip")

        mock_connection.network.ips.return_value = [tempest_ip, non_tempest_ip]

        _cleanup_networks_resources(mock_connection, "project_id")

        mock_connection.network.remove_interface_from_router.call_args_list == [
            call(tempest_router, port_id="1"),
            call(tempest_router, port_id="2"),
        ]
        mock_connection.network.delete_router.assert_called_once_with("1")
        mock_connection.network.delete_network.assert_called_once_with("1")
        mock_connection.network.delete_ip.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_instances(self, mock_connection):
        """Test cleanup instances."""
        tempest_instance = MagicMock()
        tempest_instance.configure_mock(id="1", name="tempest-server-1")
        non_tempest_instance = MagicMock()
        non_tempest_instance.configure_mock(id="2", name="non-tempest-server")
        mock_connection.compute.servers.return_value = [
            tempest_instance,
            non_tempest_instance,
        ]

        _cleanup_instances(mock_connection, "test_project")

        mock_connection.compute.delete_server.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_images(self, mock_connection):
        """Test cleanup images."""
        image_owned_by_project = MagicMock()
        image_owned_by_project.configure_mock(
            id="1", name="image-1", owner="test_project"
        )
        image_not_owned_by_project = MagicMock()
        image_not_owned_by_project.configure_mock(
            id="3", name="image-3", owner="not_test_project"
        )
        mock_connection.image.images.return_value = [
            image_owned_by_project,
            image_not_owned_by_project,
        ]

        _cleanup_images(mock_connection, "test_project")

        mock_connection.image.delete_image.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_projects(self, mock_connection):
        """Test cleanup projects."""
        project_id = "tempest_project_id"

        _cleanup_projects(mock_connection, project_id)

        mock_connection.identity.delete_project.assert_called_with(project_id)

    @patch("openstack.connection.Connection")
    def test_cleanup_users(self, mock_connection):
        """Test cleanup users."""
        tempest_user = MagicMock()
        tempest_user.configure_mock(
            domain_id="tempest", id="1", name="tempest-user-1"
        )
        non_tempest_user = MagicMock()
        non_tempest_user.configure_mock(
            domain_id="tempest", id="2", name="non-tempest-user-2"
        )

        mock_connection.identity.users.return_value = [
            tempest_user,
            non_tempest_user,
        ]

        _cleanup_users(mock_connection, domain_id="tempest")

        mock_connection.identity.delete_user.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_keypair(self, mock_connection):
        """Test cleanup keypairs."""
        tempest_keypair = MagicMock()
        tempest_keypair.configure_mock(id="1", name="tempest-keypair-1")
        non_tempest_keypair = MagicMock()
        non_tempest_keypair.configure_mock(
            id="2", name="non-tempest-keypair-2"
        )

        mock_connection.compute.keypairs.return_value = [
            tempest_keypair,
            non_tempest_keypair,
        ]

        _cleanup_keypairs(mock_connection)

        mock_connection.compute.delete_keypair.assert_called_once_with(
            tempest_keypair
        )

    @patch("openstack.connection.Connection")
    def test_cleanup_stacks_success(self, mock_connection):
        """Test cleanup heat stacks."""
        tempest_stack = MagicMock()
        tempest_stack.configure_mock(id="1", name="tempest-stack-1")
        non_tempest_stack = MagicMock()
        non_tempest_stack.configure_mock(id="2", name="non-tempest-stack-2")
        mock_connection.orchestration.stacks.return_value = [
            tempest_stack,
            non_tempest_stack,
        ]

        _cleanup_stacks(mock_connection, "test_project")

        mock_connection.orchestration.delete_stack.assert_called_once_with("1")

    @patch("openstack.connection.Connection")
    def test_cleanup_stacks_endpoint_not_found(self, mock_connection):
        """Test cleanup stacks when heat endpoint is not found."""
        mock_connection.orchestration.stacks.side_effect = EndpointNotFound

        _cleanup_stacks(mock_connection, "test_project")

        mock_connection.orchestration.delete_stack.assert_not_called()

    @patch("utils.cleanup._cleanup_instances")
    @patch("utils.cleanup._cleanup_volumes_and_snapshots")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_keypairs")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    def test_run_quick_cleanup(
        self,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_stacks,
        mock_cleanup_keypairs,
        mock_cleanup_images,
        mock_cleanup_volumes_and_snapshots,
        mock_cleanup_instances,
    ):
        """Test run quick cleanup."""
        env = MagicMock()
        mock_get_test_projects_in_domain.return_value = ["tempest_project_id"]
        run_quick_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called_once()
        mock_cleanup_stacks.assert_called_once()
        mock_cleanup_keypairs.assert_called_once()
        mock_cleanup_images.assert_called_once()
        mock_cleanup_volumes_and_snapshots.assert_called_once()
        mock_cleanup_instances.assert_called_once()

    @patch("utils.cleanup._cleanup_instances")
    @patch("utils.cleanup._cleanup_volumes_and_snapshots")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_keypairs")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    def test_run_quick_cleanup_error(
        self,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_stacks,
        mock_cleanup_keypairs,
        mock_cleanup_images,
        mock_cleanup_volumes_and_snapshots,
        mock_cleanup_instances,
    ):
        """Test run quick cleanup with failure."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = ForbiddenException

        with self.assertRaises(CleanUpError) as error:
            run_quick_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called_once()
        self.assertEqual(str(error.exception), "Operation not authorized.")

        mock_cleanup_stacks.assert_not_called()
        mock_cleanup_keypairs.assert_not_called()
        mock_cleanup_images.assert_not_called()
        mock_cleanup_volumes_and_snapshots.assert_not_called()
        mock_cleanup_instances.assert_not_called()

    @patch("utils.cleanup._cleanup_instances")
    @patch("utils.cleanup._cleanup_volumes_and_snapshots")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_keypairs")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._cleanup_networks_resources")
    @patch("utils.cleanup._cleanup_projects")
    @patch("utils.cleanup._cleanup_users")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    def test_run_extensive_cleanup(
        self,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_users,
        mock_cleanup_projects,
        mock_cleanup_networks_resources,
        mock_cleanup_stacks,
        mock_cleanup_keypairs,
        mock_cleanup_images,
        mock_cleanup_volumes_and_snapshots,
        mock_cleanup_instances,
    ):
        """Test run extensive cleanup."""
        env = MagicMock()
        mock_get_test_projects_in_domain.return_value = ["tempest_project_id"]
        run_extensive_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called_once()
        mock_cleanup_stacks.assert_called_once()
        mock_cleanup_keypairs.assert_called_once()
        mock_cleanup_images.assert_called_once()
        mock_cleanup_volumes_and_snapshots.assert_called_once()
        mock_cleanup_instances.assert_called_once()
        mock_cleanup_networks_resources.assert_called_once()
        mock_cleanup_projects.assert_called_once()
        mock_cleanup_users.assert_called_once()

    @patch("utils.cleanup._cleanup_instances")
    @patch("utils.cleanup._cleanup_volumes_and_snapshots")
    @patch("utils.cleanup._cleanup_images")
    @patch("utils.cleanup._cleanup_keypairs")
    @patch("utils.cleanup._cleanup_stacks")
    @patch("utils.cleanup._connect_to_os")
    @patch("utils.cleanup._get_test_projects_in_domain")
    def test_run_extensive_cleanup_error(
        self,
        mock_get_test_projects_in_domain,
        mock_connect_to_os,
        mock_cleanup_stacks,
        mock_cleanup_keypairs,
        mock_cleanup_images,
        mock_cleanup_volumes_and_snapshots,
        mock_cleanup_instances,
    ):
        """Test run extensive cleanup with failure."""
        env = MagicMock()
        mock_get_test_projects_in_domain.side_effect = Unauthorized

        with self.assertRaises(CleanUpError) as error:
            run_extensive_cleanup(env)

        mock_connect_to_os.assert_called_once_with(env)
        mock_get_test_projects_in_domain.assert_called_once()
        self.assertEqual(str(error.exception), "Operation not authorized.")

        mock_cleanup_stacks.assert_not_called()
        mock_cleanup_keypairs.assert_not_called()
        mock_cleanup_images.assert_not_called()
        mock_cleanup_volumes_and_snapshots.assert_not_called()
        mock_cleanup_instances.assert_not_called()


if __name__ == "__main__":
    unittest.main()
