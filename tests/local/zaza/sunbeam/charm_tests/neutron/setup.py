#!/usr/bin/env python3
#
# Copyright (c) 2025 Canonical Ltd.
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

"""Setup networks on the cloud.

Setup networks, routers, external network on the cloud.
Tempest can make use of these resources in test runs.
"""


import logging

from zaza.openstack.charm_tests.neutron.setup import (
    DEFAULT_UNDERCLOUD_NETWORK_CONFIG,
    OVERCLOUD_NETWORK_CONFIG,
)
from zaza.openstack.utilities import cli as cli_utils
from zaza.openstack.utilities import generic as generic_utils
from zaza.openstack.utilities import openstack as openstack_utils


def setup_sdn(network_config, keystone_session):
    """Perform setup for Software Defined Network.

    :param network_config: Network configuration settings dictionary
    :type network_config: dict
    :param keystone_session: Keystone session object for overcloud
    :type keystone_session: keystoneauth1.session.Session object
    :returns: None
    :rtype: None

    Copy of https://github.com/canonical/zaza-openstack-tests/blob/7fd56ccfb708ac9614eab4ccd1f29cb93325587c/zaza/openstack/configure/network.py#L94  # noqa: W505
    """
    # Get authenticated clients
    keystone_client = openstack_utils.get_keystone_session_client(
        keystone_session
    )
    neutron_client = openstack_utils.get_neutron_session_client(
        keystone_session
    )

    admin_domain = None
    # Resolve the project name from the overcloud openrc into a project id
    project_id = openstack_utils.get_project_id(
        keystone_client,
        "admin",
        domain_name=admin_domain,
    )
    # Network Setup
    subnetpools = False
    if network_config.get("subnetpool_prefix"):
        subnetpools = True

    logging.info("Configuring overcloud network")
    # Create the external network
    ext_network = openstack_utils.create_provider_network(
        neutron_client, project_id, network_config["external_net_name"]
    )

    openstack_utils.create_provider_subnet(
        neutron_client,
        project_id,
        ext_network,
        network_config["external_subnet_name"],
        network_config["default_gateway"],
        network_config["external_net_cidr"],
        network_config["start_floating_ip"],
        network_config["end_floating_ip"],
        dhcp=True,
    )
    provider_router = openstack_utils.create_provider_router(
        neutron_client, project_id
    )
    openstack_utils.plug_extnet_into_router(
        neutron_client, provider_router, ext_network
    )
    ip_version = network_config.get("ip_version") or 4
    subnetpool = None
    if subnetpools:
        address_scope = openstack_utils.create_address_scope(
            neutron_client,
            project_id,
            network_config.get("address_scope"),
            ip_version=ip_version,
        )
        subnetpool = openstack_utils.create_subnetpool(
            neutron_client,
            project_id,
            network_config.get("subnetpool_name"),
            network_config.get("subnetpool_prefix"),
            address_scope,
        )
    project_network = openstack_utils.create_project_network(
        neutron_client,
        project_id,
        shared=False,
        network_type=network_config["network_type"],
        net_name=network_config.get("project_net_name", "private"),
    )
    project_subnet = openstack_utils.create_project_subnet(
        neutron_client,
        project_id,
        project_network,
        network_config.get("private_net_cidr"),
        subnetpool=subnetpool,
        ip_version=ip_version,
        subnet_name=network_config.get(
            "project_subnet_name", "private_subnet"
        ),
    )
    openstack_utils.update_subnet_dns(
        neutron_client, project_subnet, network_config["external_dns"]
    )
    openstack_utils.plug_subnet_into_router(
        neutron_client,
        network_config["router_name"],
        project_network,
        project_subnet,
    )
    openstack_utils.add_neutron_secgroup_rules(neutron_client, project_id)


def basic_overcloud_network():
    """Run setup for neutron networking.

    Configure the overcloud network using subnet pools
    Copy of https://github.com/canonical/zaza-openstack-tests/blob/7fd56ccfb708ac9614eab4ccd1f29cb93325587c/zaza/openstack/charm_tests/neutron/setup.py#L70  # noqa: W505
    """
    cli_utils.setup_logging()

    # Get network configuration settings
    network_config = {}
    # Declared overcloud settings
    network_config.update(OVERCLOUD_NETWORK_CONFIG)
    # Default undercloud settings
    network_config.update(DEFAULT_UNDERCLOUD_NETWORK_CONFIG)

    # Environment specific settings
    network_config.update(generic_utils.get_undercloud_env_vars())

    # Get keystone session
    keystone_session = openstack_utils.get_overcloud_keystone_session()

    # Configure the overcloud network
    setup_sdn(network_config, keystone_session=keystone_session)
