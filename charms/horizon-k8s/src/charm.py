#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OpenstackDashboard Operator Charm.

This charm provide OpenstackDashboard services as part of an OpenStack
deployment
"""

import logging
from typing import (
    List,
)

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)


class WSGIDashboardPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Dashboard Pebble Handler."""

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(
                ["a2dissite", "000-default"], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2dissite warn: %s", line.strip())
            logging.debug(f"Output from a2dissite: \n{out}")
        except ops.pebble.ExecError:
            logger.exception("Failed to disable default site in apache")
        super().init_service(context)


class OpenstackDashboardOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "openstack-dashboard"
    wsgi_admin_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi"
    )
    wsgi_public_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi"
    )

    db_sync_cmds = [
        [
            "python3",
            "/usr/share/openstack-dashboard/manage.py",
            "migrate",
            "--noinput",
        ]
    ]

    mandatory_relations = {
        "database",
        "ingress-public",
        "cloud-credentials",
    }

    @property
    def default_public_ingress_port(self):
        """Default public ingress port."""
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
        return "horizon"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "horizon"

    @property
    def service_endpoints(self):
        """Endpoints for horizon."""
        return [
            {
                "service_name": "openstack-dashboard",
                "type": "openstack-dashboard",
                "description": "OpenStack OpenstackDashboard API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            WSGIDashboardPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Configure charm services."""
        super().configure_charm(event)
        if self.bootstrapped():
            self.unit.status = ops.model.ActiveStatus(self.ingress_public.url)


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OpenstackDashboardOperatorCharm, use_juju_for_storage=True)
