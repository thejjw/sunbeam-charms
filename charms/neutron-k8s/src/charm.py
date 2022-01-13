#!/usr/bin/env python3
"""Neutron Operator Charm.

This charm provide Neutron services as part of an OpenStack deployment
"""

import logging
from typing import List

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.cprocess as sunbeam_cprocess
import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.container_handlers as sunbeam_chandlers
import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

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

    db_sync_cmds = [
        ['sudo', '-u', 'neutron', 'neutron-db-manage', '--config-file',
         '/etc/neutron/neutron.conf', '--config-file',
         '/etc/neutron/plugins/ml2/ml2_conf.ini', 'upgrade', 'head']]

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('public', self.default_public_ingress_port),
            ]
        )

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


class NeutronWallabyOperatorCharm(NeutronOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(NeutronWallabyOperatorCharm, use_juju_for_storage=True)
