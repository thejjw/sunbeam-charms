#!/usr/bin/env python3
"""OpenstackDashboard Operator Charm.

This charm provide OpenstackDashboard services as part of an OpenStack
deployment
"""

import logging
from typing import List

import ops.framework
from ops.main import main

import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers

logger = logging.getLogger(__name__)


class WSGIDashboardPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(
                ['a2dissite', '000-default'],
                timeout=5*60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning('a2dissite warn: %s', line.strip())
            logging.debug(f'Output from a2dissite: \n{out}')
        except ops.pebble.ExecError:
            logger.exception(
                "Failed to disable default site in apache"
            )
        super().init_service(context)


class OpenstackDashboardOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "openstack-dashboard"
    wsgi_admin_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi")
    wsgi_public_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi")

    db_sync_cmds = [
        [
            'python3',
            '/usr/share/openstack-dashboard/manage.py',
            'migrate',
            '--noinput']
    ]

    mandatory_relations = {
        'database',
        'ingress-public',
        'cloud-credentials',
    }

    @property
    def default_public_ingress_port(self):
        return 80

    @property
    def apache_vhost(self) -> str:
        """Service default configuration file."""
        return "/etc/apache2/sites-enabled/openstack-dashboard.conf"

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/openstack-dashboard/local_settings.py"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'horizon'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'horizon'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'openstack-dashboard',
                'type': 'openstack-dashboard',
                'description': "OpenStack OpenstackDashboard API",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            WSGIDashboardPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Configure charm services."""
        super().configure_charm(event)
        if self.bootstrapped():
            self.unit.status = ops.model.ActiveStatus(
                self.ingress_public.url)


class OpenstackDashboardXenaOperatorCharm(OpenstackDashboardOperatorCharm):

    openstack_release = 'xena'


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OpenstackDashboardXenaOperatorCharm, use_juju_for_storage=True)
