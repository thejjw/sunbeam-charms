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

This charm provides Ironic API service as part of an OpenStack
deployment.
"""

import logging
import socket
from typing import (
    Dict,
    List,
    Mapping,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops.charm import (
    RelationEvent,
)

logger = logging.getLogger(__name__)

IRONIC_API_PORT = 6385
IRONIC_API_CONTAINER = "ironic-api"
IRONIC_API_PROVIDES = "ironic-api"


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
        ]
        return pebble_handlers

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        super().post_config_setup()
        self.set_readiness_on_related_units()

    def handle_readiness_request_from_event(
        self, event: RelationEvent
    ) -> None:
        """Set service readiness in relation data."""
        self.svc_ready_handler.interface.set_service_status(
            event.relation, self.bootstrapped()
        )

    def set_readiness_on_related_units(self) -> None:
        """Set service readiness on ironic-api related units."""
        logger.debug(
            "Set service readiness on all connected placement relations"
        )
        for relation in self.framework.model.relations[IRONIC_API_PROVIDES]:
            self.svc_ready_handler.interface.set_service_status(relation, True)

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
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/rootwrap.conf",
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

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for operator."""
        handlers = super().get_relation_handlers(handlers or [])

        self.svc_ready_handler = (
            sunbeam_rhandlers.ServiceReadinessProviderHandler(
                self,
                IRONIC_API_PROVIDES,
                self.handle_readiness_request_from_event,
            )
        )
        handlers.append(self.svc_ready_handler)

        return handlers


if __name__ == "__main__":  # pragma: nocover
    ops.main(IronicOperatorCharm)
