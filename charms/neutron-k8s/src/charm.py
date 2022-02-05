#!/usr/bin/env python3
"""Neutron Operator Charm.

This charm provide Neutron services as part of an OpenStack deployment
"""

import logging
from typing import List

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.container_handlers as sunbeam_chandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

logger = logging.getLogger(__name__)


class NeutronServerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):

    def get_layer(self):
        """Neutron server service

        :returns: pebble layer configuration for neutron server service
        :rtype: dict
        """
        return {
            "summary": "neutron server layer",
            "description": "pebble configuration for neutron server",
            "services": {
                "neutron-server": {
                    "override": "replace",
                    "summary": "Neutron Server",
                    "command": "neutron-server",
                    "startup": "enabled"
                }
            }
        }

    def default_container_configs(self):
        return [
            sunbeam_core.ContainerConfigFile(
                [self.container_name],
                '/etc/neutron/neutron.conf',
                'neutron',
                'neutron')]


class NeutronOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "neutron-server"
    # Remove wsgi_admin_script and wsgi_admin_script after aso fix
    wsgi_admin_script = ""
    wsgi_public_script = ""

    db_sync_cmds = [
        ['sudo', '-u', 'neutron', 'neutron-db-manage', '--config-file',
         '/etc/neutron/neutron.conf', '--config-file',
         '/etc/neutron/plugins/ml2/ml2_conf.ini', 'upgrade', 'head']]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            NeutronServerPebbleHandler(
                self,
                'neutron-server',
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm,
            )
        ]

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'neutron',
                'type': 'network',
                'description': "OpenStack Networking",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    @property
    def default_public_ingress_port(self):
        return 9696

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'neutron'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'neutron'

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/neutron/neutron.conf"


# Neutron OVN Specific Code

class OVNContext(sunbeam_ctxts.ConfigContext):

    def context(self) -> dict:
        return {
            'extension_drivers': 'port_security',
            'type_drivers': 'geneve,gre,vlan,flat,local',
            'tenant_network_types': 'geneve,gre,vlan,flat,local',
            'mechanism_drivers': 'ovn',
            'path_mtu': '1500',
            'tunnel_id_ranges': '1:1000',
            'vni_ranges': '1001:2000',
            'network_vlan_ranges': 'physnet1:1000:2000',
            'flat_networks': 'physnet1',
            'enable_tunneling': 'True',
            'local_ip': '127.0.0.1',
            'tunnel_types': 'gre',
            'enable_security_group': 'True',
            'vni_ranges': '1001:2000',
            'max_header_size': '38',
            'ovn_l3_scheduler': 'leastloaded',
            'ovn_metadata_enabled': 'True',
            'enable_distributed_floating_ip': 'False',
            'dns_servers': '',
            'dhcp_default_lease_time': '43200',
            'dns_servers': '',
            'ovn_dhcp4_global_options': '',
            'ovn_dhcp6_global_options': '',
            'vhost_sock_dir': '/run/libvirt-vhost-user',
            'ovn_key': '/etc/neutron/plugins/ml2/key_host',
            'ovn_cert': '/etc/neutron/plugins/ml2/cert_host',
            'ovn_ca_cert': '/etc/neutron/plugins/ml2/neutron-ovn.crt'}


class NeutronServerOVNPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):

    def default_container_configs(self):
        return [
            sunbeam_core.ContainerConfigFile(
                '/etc/neutron/neutron.conf',
                'neutron',
                'neutron'),
            sunbeam_core.ContainerConfigFile(
                '/etc/neutron/plugins/ml2/key_host',
                'root',
                'root'),
            sunbeam_core.ContainerConfigFile(
                '/etc/neutron/plugins/ml2/cert_host',
                'root',
                'root'),
            sunbeam_core.ContainerConfigFile(
                '/etc/neutron/plugins/ml2/neutron-ovn.crt',
                'root',
                'root'),
            sunbeam_core.ContainerConfigFile(
                '/etc/neutron/plugins/ml2/ml2_conf.ini',
                'root',
                'root')]


class NeutronOVNOperatorCharm(NeutronOperatorCharm):

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            OVNContext(self, "ovn"))
        return contexts

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            NeutronServerOVNPebbleHandler(
                self,
                'neutron-server',
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm,
            )
        ]


class NeutronOVNWallabyOperatorCharm(NeutronOVNOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(NeutronOVNWallabyOperatorCharm, use_juju_for_storage=True)
