#!/usr/bin/env python3
"""Placement Operator Charm.

This charm provide Placement services as part of an OpenStack deployment
"""

import logging
from typing import List

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.cprocess as sunbeam_cprocess
import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

logger = logging.getLogger(__name__)


class PlacementOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "placement-api"
    wsgi_admin_script = '/usr/bin/placement-wsgi-api'
    wsgi_public_script = '/usr/bin/placement-wsgi-api'

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('public', self.default_public_ingress_port),
            ]
        )

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/placement/placement.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'placement'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'placement'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'placement',
                'type': 'placement',
                'description': "OpenStack Placement API",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    @property
    def default_public_ingress_port(self):
        return 8778

    def _do_bootstrap(self):
        """
        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
        """
        super()._do_bootstrap()
        try:
            container = self.unit.get_container(self.wsgi_container_name)
            logger.info("Syncing database...")
            out = sunbeam_cprocess.check_output(
                container,
                [
                    'sudo', '-u', 'placement',
                    'placement-manage', 'db', 'sync'],
                service_name='placement-db-sync',
                timeout=180)
            logging.debug(f'Output from database sync: \n{out}')
        except sunbeam_cprocess.ContainerProcessError:
            logger.exception('Failed to bootstrap')
            self._state.bootstrapped = False
            return

class PlacementWallabyOperatorCharm(PlacementOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(PlacementWallabyOperatorCharm, use_juju_for_storage=True)
