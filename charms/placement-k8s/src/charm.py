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


"""Placement Operator Charm.

This charm provide Placement services as part of an OpenStack deployment
"""

import json
import logging
import urllib.error
import urllib.request
from typing import (
    List,
)

import ops
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops.charm import (
    RelationEvent,
)
from ops.framework import (
    StoredState,
)

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class WSGIPlacementPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Placement Pebble Handler."""

    charm: "PlacementOperatorCharm"

    def _on_update_status(self, event: ops.EventBase) -> None:
        """Refresh container health and publish readiness once the API serves."""
        if self.check_readiness(event):
            self.charm.set_readiness_on_related_units()

    def check_readiness(self, event: ops.EventBase) -> bool:
        """Assess Pebble and API health using the existing container status."""
        super()._on_update_status(event)
        return self.check_api_readiness()

    def check_api_readiness(self) -> bool:
        """Check the API after service setup or a successful Pebble assessment."""
        if (
            self.charm.is_service_paused
            or not self.charm.bootstrapped()
            or not all(
                handler.ready
                for handler in self.charm.relation_handlers
                if handler.mandatory
            )
            or not isinstance(self.status.status, ops.ActiveStatus)
        ):
            return False

        if not self.charm._placement_api_healthy():
            self.status.set(ops.WaitingStatus("Placement API not yet serving"))
            return False

        return True

    def stop_all(self) -> None:
        """Stop services and report maintenance when intentionally paused."""
        super().stop_all()
        if self.charm.is_service_paused:
            self.status.set(ops.MaintenanceStatus("Service paused"))

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(
                ["a2dissite", "placement-api"], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2dissite warn: %s", line.strip())
            logging.debug(f"Output from a2dissite: \n{out}")
        except ops.pebble.ExecError:
            logger.exception("Failed to disable placement-api site in apache")
        super().init_service(context)


@sunbeam_tracing.trace_sunbeam_charm
class PlacementOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "placement-api"
    wsgi_admin_script = "/usr/bin/placement-api"
    wsgi_public_script = "/usr/bin/placement-api"

    db_sync_cmds = [
        ["sudo", "-u", "placement", "placement-manage", "db", "sync"]
    ]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        self.placement_pebble_handler = WSGIPlacementPebbleHandler(
            self,
            self.service_name,
            self.service_name,
            self.container_configs,
            self.template_dir,
            self.configure_charm,
            f"wsgi-{self.service_name}",
        )
        return [self.placement_pebble_handler]

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        self.svc_ready_handler = (
            sunbeam_rhandlers.ServiceReadinessProviderHandler(
                self,
                "placement",
                self.handle_readiness_request_from_event,
            )
        )
        handlers.append(self.svc_ready_handler)

        handlers = super().get_relation_handlers(handlers)
        return handlers

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        super().post_config_setup()
        if self.placement_pebble_handler.check_api_readiness():
            self.set_readiness_on_related_units()

    def handle_readiness_request_from_event(
        self, event: RelationEvent
    ) -> None:
        """Set service readiness in relation data."""
        self.svc_ready_handler.interface.set_service_status(
            event.relation,
            self.placement_pebble_handler.check_readiness(event),
        )

    def _placement_api_healthy(self) -> bool:
        """Check that the placement API is actually serving on its local port.

        Returns True if placement-api responds with valid version data,
        False otherwise.  The check uses localhost so it is not subject to
        traefik route availability.
        """
        url = f"http://localhost:{self.default_public_ingress_port}/"
        req = urllib.request.Request(
            url, headers={"Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return "versions" in data
        except Exception as e:
            logger.debug("Placement API health check failed: %s", e)
            return False

    def set_readiness_on_related_units(self) -> None:
        """Publish readiness after the Pebble handler confirms API health."""
        logger.debug(
            "Set service readiness on all connected placement relations"
        )
        for relation in self.framework.model.relations["placement"]:
            self.svc_ready_handler.interface.set_service_status(relation, True)

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                self.service_conf,
                "root",
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                self.service_group,
                0o640,
            ),
        ]
        return _cconfigs

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/placement/placement.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "placement"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "placement"

    @property
    def service_endpoints(self):
        """Service endpoints description."""
        return [
            {
                "service_name": "placement",
                "type": "placement",
                "description": "OpenStack Placement API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default ingress port."""
        return 8778


if __name__ == "__main__":  # pragma: no cover
    ops.main(PlacementOperatorCharm)
