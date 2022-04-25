#!/usr/bin/env python3
"""Placement Operator Charm.

This charm provide Placement services as part of an OpenStack deployment
"""

import logging

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm

logger = logging.getLogger(__name__)


class PlacementOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "placement-api"
    wsgi_admin_script = '/usr/bin/placement-api'
    wsgi_public_script = '/usr/bin/placement-api'

    db_sync_cmds = [
        ['sudo', '-u', 'placement', 'placement-manage', 'db', 'sync']]

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


class PlacementXenaOperatorCharm(PlacementOperatorCharm):

    openstack_release = 'xena'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(PlacementXenaOperatorCharm, use_juju_for_storage=True)
