from openstack import connection


def _get_test_projects_in_domain(conn, domain_id, prefix):
    """Get all projects with names starting with prefix in the specified domain."""
    projects = []
    for project in conn.identity.projects(domain_id=domain_id):
        projects.append(project.id)
    return projects

def _cleanup_instances(conn, project_id, prefix='tempest-'):
    """Delete instances with names starting with prefix in the specified project."""
    for server in conn.compute.servers(project_id=project_id):
        if server.name.startswith(prefix):
            conn.compute.delete_server(server.id)

def _cleanup_keypairs(conn, prefix='tempest-'):
    """Delete keypairs with names starting with prefix."""
    for keypair in conn.compute.keypairs():
        if keypair.name.startswith(prefix):
            conn.compute.delete_keypair(keypair)

def _cleanup_volumes_and_snapshots(conn, project_id, prefix='tempest-'):
    """Delete volumes and snapshots with names starting with prefix in the specified project."""
    for snapshot in conn.block_store.snapshots(details=True, project_id=project_id):
        if snapshot.name.startswith(prefix):
            conn.block_store.delete_snapshot(snapshot.id)
    for volume in conn.block_store.volumes(details=True, project_id=project_id):
        if volume.name.startswith(prefix):
            conn.block_store.delete_volume(volume.id)

def _cleanup_images(conn, project_id, prefix='tempest-'):
    """Delete images with names starting with prefix and owned by the specified project."""
    for image in conn.image.images():
        # TODO: to be extra careful, we should also check the prefix of the image
        # However, some tempest tests are not creating images with the prefix, so
        # we should wait until https://review.opendev.org/c/openstack/tempest/+/908358
        # is released.
        if image.owner == project_id:
            conn.image.delete_image(image.id)

def _cleanup_networks_resources(conn, project_id, prefix='tempest-'):
    """Delete ports, routers. and networks with names starting with prefix in the specified project."""
    
    # Delete ports and routers
    for router in conn.network.routers(project_id=project_id):
        if router.name.startswith(prefix):
            for port in conn.network.ports(device_id=router.id):
                conn.network.remove_interface_from_router(router, port_id=port.id)
            conn.network.delete_router(router.id)
    
    # Delete networks
    for network in conn.network.networks(project_id=project_id):
        if network.name.startswith(prefix):
            conn.network.delete_network(network.id)
    
    # Cleanup floating IPs
    for ip in conn.network.ips(project_id=project_id):
        if ip.description and ip.description.startswith(prefix):
            conn.network.delete_ip(ip.id)

def _cleanup_users(conn, domain_id, prefix='tempest-'):
    """Delete users with names starting with prefix in the specified domain."""
    for user in conn.identity.users(domain_id=domain_id):
        if user.name.startswith(prefix):
            conn.identity.delete_user(user.id)

def _cleanup_projects(conn, project_id):
    """Delete projects in the specified domain."""
    conn.identity.delete_project(project_id)

def run_quick_cleanup(conn, prefix, projects):
    for project_id in projects:
        # Cleanup instances
        _cleanup_instances(conn, project_id, prefix)

        # Cleanup volumes and snapshots
        _cleanup_volumes_and_snapshots(conn, project_id, prefix)

        # Cleanup images
        _cleanup_images(conn, project_id, prefix)

        # Clean up keypairs
        _cleanup_keypairs(conn, prefix)


def run_extensive_cleanup(conn, prefix, projects, domain_id):
    for project_id in projects:
        # Cleanup instances
        _cleanup_instances(conn, project_id, prefix)

        # Cleanup volumes and snapshots
        _cleanup_volumes_and_snapshots(conn, project_id, prefix)

        # Cleanup images
        _cleanup_images(conn, project_id, prefix)

        # Clean up keypairs
        _cleanup_keypairs(conn, prefix)

        # Cleanup networks, routers, and ports
        _cleanup_networks_resources(conn, project_id, prefix)

        # Cleanup projects
        _cleanup_projects(conn, project_id)

    # Cleanup users
    _cleanup_users(conn, domain_id, prefix)

def perform_cleanup(env, extensive_cleanup=False, prefix='tempest-'):
    """Perform the cleanup of tempest test resources under a specific domain."""
    # Establish connection to the OpenStack cloud
    conn = connection.Connection(
        auth_url=env.OS_AUTH_URL,
        project_name=env.OS_PROJECT_NAME,
        username=env.OS_USERNAME,
        password=env.OS_PASSWORD,
        user_domain_id=env.OS_USER_DOMAIN_ID,
        project_domain_id=env.OS_USER_DOMAIN_ID,
    )

    test_projects_in_domain = _get_test_projects_in_domain(conn, env.domain_id, prefix)

    # TODO: catch possible exceptions
    if extensive_cleanup:
        run_extensive_cleanup(conn, prefix, test_projects_in_domain, env.domain_id)
    else:
        run_quick_cleanup(conn, prefix, test_projects_in_domain)