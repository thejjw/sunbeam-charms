#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Masakari Operator Charm.

This charm provide Masakari services as part of an OpenStack deployment
"""

import logging

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.tracing as sunbeam_tracing
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

MASAKARI_API_CONTAINER = "masakari-api"
MASAKARI_ENGINE_CONTAINER = "masakari-engine"


@sunbeam_tracing.trace_type
class MasakariWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for Masakari API container."""

    # Overwrite the masakari-api.conf file from the rock
    @property
    def wsgi_conf(self) -> str:
        """Location of WSGI config file."""
        return f"/etc/apache2/sites-available/{self.service_name}.conf"


@sunbeam_tracing.trace_type
class MasakariEnginePebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Masakari Engine container."""

    def get_layer(self):
        """Pebble layer for Masakari Engine service.

        :returns: pebble service layer config for masakari engine service
        :rtype: dict
        """
        return {
            "summary": "masakari engine layer",
            "description": "pebble configuration for masakari engine service",
            "services": {
                "masakari-engine": {
                    "override": "replace",
                    "summary": "masakari engine",
                    "command": "masakari-engine",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class MasakariOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    mandatory_relations = {
        "database",
        "amqp",
        "identity-service",
        "ingress-public",
    }

    wsgi_admin_script = "/usr/bin/masakari-wsgi"
    wsgi_public_script = "/usr/bin/masakari-wsgi"

    db_sync_cmds = [
        [
            "masakari-manage",
            "db",
            "sync",
        ]
    ]

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    path="/usr/local/share/ca-certificates/ca-bundle.pem",
                    user="root",
                    group=self.service_group,
                    permissions=0o640,
                ),
            ]
        )
        return _cconfigs

    def get_pebble_handlers(self):
        """Pebble handlers for operator."""
        pebble_handlers = []
        pebble_handlers.extend(
            [
                MasakariWSGIPebbleHandler(
                    self,
                    MASAKARI_API_CONTAINER,
                    self.service_name,
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                    self.service_name,
                ),
                MasakariEnginePebbleHandler(
                    self,
                    MASAKARI_ENGINE_CONTAINER,
                    "masakari-engine",
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                ),
            ]
        )
        return pebble_handlers

    @property
    def service_name(self):
        """Service name."""
        return "masakari-api"

    @property
    def service_conf(self):
        """Service default configuration file."""
        return "/etc/masakari/masakari.conf"

    @property
    def service_user(self):
        """Service user file and directory ownership."""
        return "masakari"

    @property
    def service_group(self):
        """Service group file and directory ownership."""
        return "masakari"

    @property
    def service_endpoints(self):
        """Return masakari service endpoints."""
        return [
            {
                "service_name": "masakari",
                "type": "instance-ha",
                "description": "OpenStack Masakari API",
                "internal_url": f"{self.internal_url}/v1/$(tenant_id)s",
                "public_url": f"{self.public_url}/v1/$(tenant_id)s",
                "admin_url": f"{self.admin_url}/v1/$(tenant_id)s",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default port."""
        return 15868


if __name__ == "__main__":
    main(MasakariOperatorCharm)
