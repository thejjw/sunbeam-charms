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

"""Ironic Operator Charm.

This charm provides Ironic API and noVNC Proxy services as part of an OpenStack
deployment.
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

IRONIC_API_PORT = 6385
IRONIC_API_CONTAINER = "ironic-api"
IRONIC_NOVNCPROXY_CONTAINER = "ironic-novncproxy"


@sunbeam_tracing.trace_type
class IronicNoVNCProxyPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Ironic noVNC Proxy."""

    def get_layer(self) -> dict:
        """Ironic noVNC Proxy service layer.

        :returns: pebble layer configuration for the ironic-novncproxy service
        :rtype: dict
        """
        return {
            "summary": "ironic novncproxy layer",
            "description": "pebble configuration for ironic-novncproxy service",
            "services": {
                "ironic-novncproxy": {
                    "override": "replace",
                    "summary": "Ironic noVNC Proxy",
                    "command": "ironic-novncproxy",
                    "user": "ironic",
                    "group": "ironic",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for handler."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/ironic.conf",
                "root",
                "ironic",
                0o640,
            ),
        ]
        return _cconfigs


@sunbeam_tracing.trace_sunbeam_charm
class IronicOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    service_name = "ironic-api"
    wsgi_admin_script = "/usr/bin/ironic-api-wsgi"
    wsgi_public_script = "/usr/bin/ironic-api-wsgi"

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "ironic",
            "ironic-dbsync",
        ],
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/ironic/ironic.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "ironic"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "ironic"

    @property
    def service_endpoints(self) -> List[Dict]:
        """Service endpoints for the Ironic API services."""
        return [
            {
                "service_name": "ironic",
                "type": "baremetal",
                "description": "OpenStack Ironic bare metal provisioner",
                "internal_url": self.internal_url,
                "public_url": self.public_url,
                "admin_url": self.admin_url,
            },
        ]

    @property
    def default_public_ingress_port(self):
        """Public ingress port for service."""
        return IRONIC_API_PORT

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
        """Provide database name for ironic services."""
        return {"database": "ironic"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                IRONIC_API_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            IronicNoVNCProxyPebbleHandler(
                self,
                IRONIC_NOVNCPROXY_CONTAINER,
                IRONIC_NOVNCPROXY_CONTAINER,
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
                "/etc/ironic/api_audit_map.conf",
                "root",
                "ironic",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/ironic.conf",
                "root",
                "ironic",
                0o640,
            ),
        ]
        return _cconfigs

    @property
    def db_sync_container_name(self) -> str:
        """Name of Container to run db sync from."""
        return IRONIC_API_CONTAINER


if __name__ == "__main__":  # pragma: nocover
    ops.main(IronicOperatorCharm)
