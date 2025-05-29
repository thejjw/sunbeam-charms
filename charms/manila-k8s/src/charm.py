#!/usr/bin/env python3
#
# Copyright 2025 Canonical Ltd.
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

"""Manila Operator Charm.

This charm provides Manila services as part of an OpenStack deployment.
"""

import logging
from typing import (
    Dict,
    List,
    Mapping,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

MANILA_API_PORT = 8786
MANILA_API_CONTAINER = "manila-api"
MANILA_SCHEDULER_CONTAINER = "manila-scheduler"


@sunbeam_tracing.trace_type
class ManilaSchedulerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Manila Scheduler."""

    def get_layer(self) -> dict:
        """Manila Scheduler service layer.

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "manila scheduler layer",
            "description": "pebble configuration for manila services",
            "services": {
                "manila-scheduler": {
                    "override": "replace",
                    "summary": "Manila Scheduler",
                    "command": "manila-scheduler",
                    "user": "manila",
                    "group": "manila",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/manila.conf",
                "root",
                "manila",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "manila",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_sunbeam_charm
class ManilaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    service_name = "manila-api"
    wsgi_admin_script = "/usr/bin/manila-wsgi"
    wsgi_public_script = "/usr/bin/manila-wsgi"

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "manila",
            "manila-manage",
            "db",
            "sync",
        ],
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/manila/manila.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "manila"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "manila"

    @property
    def service_endpoints(self) -> List[Dict]:
        """Service endpoints for the Manila API services."""
        return [
            {
                "service_name": "manilav2",
                "type": "sharev2",
                "description": "OpenStack Shared File Systems V2",
                "internal_url": f"{self.internal_url}/v2",
                "public_url": f"{self.public_url}/v2",
                "admin_url": f"{self.admin_url}/v2",
            },
        ]

    @property
    def default_public_ingress_port(self):
        """Public ingress port for service."""
        return MANILA_API_PORT

    @property
    def ingress_healthcheck_path(self):
        """Healthcheck path for ingress relation."""
        return "/healthcheck"

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return (
            f"http://localhost:{self.default_public_ingress_port}/healthcheck"
        )

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for manila services."""
        return {"database": "manila"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                MANILA_API_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            ManilaSchedulerPebbleHandler(
                self,
                MANILA_SCHEDULER_CONTAINER,
                "manila-scheduler",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/manila.conf",
                "root",
                "manila",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/api-paste.ini",
                "root",
                "manila",
                0o640,
            ),
        ]
        return _cconfigs

    def set_config_from_event(self, event: ops.framework.EventBase) -> None:
        """Set config in relation data."""
        pass

    @property
    def db_sync_container_name(self) -> str:
        """Name of Container to run db sync from."""
        return MANILA_SCHEDULER_CONTAINER


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaOperatorCharm)
