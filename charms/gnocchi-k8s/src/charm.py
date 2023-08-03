#!/usr/bin/env python3
"""Gnocchi Operator Charm.

This charm provide Gnocchi services as part of an OpenStack deployment
"""

import logging

from ops.framework import StoredState
from ops.main import main

import ops_sunbeam.charm as sunbeam_charm

logger = logging.getLogger(__name__)


class GnocchiOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "gnocchi-api"
    wsgi_admin_script = '/usr/bin/gnocchi-api-wsgi'
    wsgi_public_script = '/usr/bin/gnocchi-api-wsgi'

    db_sync_cmds = [
        ['/snap/bin/gnocchi.upgrade', '--log-file=/var/snap/gnocchi/common/log/gnocchi-upgrade.log']
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/gnocchi/gnocchi.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'gnocchi'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'gnocchi'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'gnocchi',
                'type': 'gnocchi',
                'description': "OpenStack Gnocchi API",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    @property
    def default_public_ingress_port(self):
        return 8041


if __name__ == "__main__":
    main(GnocchiOperatorCharm)
