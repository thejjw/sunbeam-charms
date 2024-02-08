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
"""Utils for cleaning up tempest-related resources."""
import os

from keystoneauth1.exceptions.catalog import (
    EndpointNotFound,
)
from keystoneauth1.exceptions.http import (
    Unauthorized,
)
from openstack import (
    connection,
)
from openstack.exceptions import (
    ForbiddenException,
)

RESOURCE_PREFIX = "tempest-"


class CleanUpError(Exception):
    """Exception raised when clean-up process terminated unsuccessfully."""


def _connect_to_os(env) -> connection.Connection:
    """Establish connection to the OpenStack cloud."""
    return connection.Connection(
        auth_url=env["OS_AUTH_URL"],
        project_name=env["OS_PROJECT_NAME"],
        username=env["OS_USERNAME"],
        password=env["OS_PASSWORD"],
        user_domain_id=env["OS_USER_DOMAIN_ID"],
        project_domain_id=env["OS_USER_DOMAIN_ID"],
    )


def _get_test_projects_in_domain(
    conn, domain_id, prefix=RESOURCE_PREFIX
) -> list[str]:
    """Get all projects with names starting with prefix in the specified domain."""
    return [
        project.id
        for project in conn.identity.projects(domain_id=domain_id)
        if project.name.startswith(prefix)
    ]


def _cleanup_compute_resources(
    conn, project_id, prefix=RESOURCE_PREFIX
) -> None:
    """Delete compute resources with names starting with prefix in the specified project.

    The compute resources to be removed are instances and keypairs.
    """
    # Delete instances
    for server in conn.compute.servers(project_id=project_id):
        if server.name.startswith(prefix):
            conn.compute.delete_server(server.id)

    # Delete keypairs
    for keypair in conn.compute.keypairs():
        if keypair.name.startswith(prefix):
            conn.compute.delete_keypair(keypair)


def _cleanup_block_resources(conn, project_id, prefix=RESOURCE_PREFIX) -> None:
    """Delete block storage resources with names starting with prefix in the specified project.

    The block storage resources to be removed are snapshots and instances.
    """
    # Delete snapshots
    for snapshot in conn.block_store.snapshots(
        details=True, project_id=project_id
    ):
        if snapshot.name.startswith(prefix):
            conn.block_store.delete_snapshot(snapshot.id)

    # Delete volumes
    for volume in conn.block_store.volumes(
        details=True, project_id=project_id
    ):
        if volume.name.startswith(prefix):
            conn.block_store.delete_volume(volume.id)


def _cleanup_images(conn, project_id, prefix=RESOURCE_PREFIX) -> None:
    """Delete images with names starting with prefix and owned by the specified project."""
    for image in conn.image.images():
        # TODO: to be extra careful, we should also check the prefix of the image
        # However, some tempest tests are not creating images with the prefix, so
        # we should wait until https://review.opendev.org/c/openstack/tempest/+/908358
        # is released.
        if image.owner == project_id:
            conn.image.delete_image(image.id)


def _cleanup_networks_resources(
    conn, project_id, prefix=RESOURCE_PREFIX
) -> None:
    """Delete network resources with names starting with prefix in the specified project.

    The network resources to be removed are ports, routers, and networks.
    """
    # Delete ports and routers
    for router in conn.network.routers(project_id=project_id):
        if router.name.startswith(prefix):
            for port in conn.network.ports(device_id=router.id):
                conn.network.remove_interface_from_router(
                    router, port_id=port.id
                )
            conn.network.delete_router(router.id)

    # Delete networks
    for network in conn.network.networks(project_id=project_id):
        if network.name.startswith(prefix):
            conn.network.delete_network(network.id)


def _cleanup_stacks(conn, project_id, prefix=RESOURCE_PREFIX) -> None:
    """Delete stacks with names starting with prefix and owned by the specified project.

    If Heat service is not found in the cloud, this clean-up will be skipped.
    """
    try:
        for stack in conn.orchestration.stacks(project_id=project_id):
            if stack.name.startswith(prefix):
                conn.orchestration.delete_stack(stack.id)
    except EndpointNotFound:
        # do nothing if the heat endpoint is not found
        pass


def _cleanup_users(conn, domain_id, prefix=RESOURCE_PREFIX) -> None:
    """Delete users with names starting with prefix in the specified domain."""
    for user in conn.identity.users(domain_id=domain_id):
        if user.name.startswith(prefix):
            conn.identity.delete_user(user.id)


def _cleanup_projects(conn, project_id) -> None:
    """Delete projects in the specified domain."""
    conn.identity.delete_project(project_id)


def run_quick_cleanup(env, prefix=RESOURCE_PREFIX) -> None:
    """Perform the quick cleanup of tempest resources under a specific domain.

    This clean-up removes compute instances, keypairs, volumes, snapshots,
    images, and stacks.
    """
    conn = _connect_to_os(env)

    try:
        projects = _get_test_projects_in_domain(
            conn, env["OS_DOMAIN_ID"], prefix
        )

        for project_id in projects:
            # Cleanup compute resources
            _cleanup_compute_resources(conn, project_id, prefix)

            # Cleanup block storage resources
            _cleanup_block_resources(conn, project_id, prefix)

            # Cleanup images
            _cleanup_images(conn, project_id, prefix)

            # Clean up heat stacks
            _cleanup_stacks(conn, prefix)

    except (ForbiddenException, Unauthorized) as e:
        raise CleanUpError("Operation not authorized.") from e


def run_extensive_cleanup(env, prefix=RESOURCE_PREFIX) -> None:
    """Perform the extensive cleanup of tempest resources under a specific domain.

    This clean-up removes compute instances, keypairs, volumes, snapshots,
    images, and stacks, as well as generated test accounts, projects, and the
    network resources associated with them.
    """
    conn = _connect_to_os(env)
    try:
        projects = _get_test_projects_in_domain(
            conn, env["OS_DOMAIN_ID"], prefix
        )

        for project_id in projects:
            # Cleanup compute resources
            _cleanup_compute_resources(conn, project_id, prefix)

            # Cleanup block storage resources
            _cleanup_block_resources(conn, project_id, prefix)

            # Cleanup images
            _cleanup_images(conn, project_id, prefix)

            # Clean up heat stacks
            _cleanup_stacks(conn, prefix)

            # Cleanup networks, routers, and ports
            _cleanup_networks_resources(conn, project_id, prefix)

            # Cleanup projects
            _cleanup_projects(conn, project_id)

        # Cleanup users
        _cleanup_users(conn, env["OS_DOMAIN_ID"], prefix)

    except (ForbiddenException, Unauthorized) as e:
        raise CleanUpError("Operation not authorized.") from e


def main() -> None:
    """Entrypoint for executing the script directly.

    This will be used in periodic test runs.
    Quick cleanup will be performed.
    """
    env = {
        "OS_AUTH_URL": os.getenv("OS_AUTH_URL", ""),
        "OS_USERNAME": os.getenv("OS_USERNAME", ""),
        "OS_PASSWORD": os.getenv("OS_PASSWORD", ""),
        "OS_PROJECT_NAME": os.getenv("OS_PROJECT_NAME", ""),
        "OS_DOMAIN_ID": os.getenv("OS_DOMAIN_ID", ""),
        "OS_USER_DOMAIN_ID": os.getenv("OS_USER_DOMAIN_ID", ""),
        "OS_PROJECT_DOMAIN_ID": os.getenv("OS_PROJECT_DOMAIN_ID", ""),
    }

    run_quick_cleanup(env)


if __name__ == "__main__":
    main()
