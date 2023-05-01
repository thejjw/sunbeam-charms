#!/usr/bin/env python3
"""Heat Operator Charm.

This charm provide Heat services as part of an OpenStack deployment
"""

import logging

from ops.framework import StoredState
from ops.main import main

import ops_sunbeam.charm as sunbeam_charm

logger = logging.getLogger(__name__)


class HeatOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "heat-api"
    wsgi_admin_script = '/usr/bin/heat-wsgi-api'
    wsgi_public_script = '/usr/bin/heat-wsgi-api'

    db_sync_cmds = [
        ['heat-manage', 'db_sync']
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/heat/heat.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'heat'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'heat'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'heat',
                'type': 'heat',
                'description': "OpenStack Heat API",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    @property
    def default_public_ingress_port(self):
        return 8004


if __name__ == "__main__":
    main(HeatOperatorCharm)
