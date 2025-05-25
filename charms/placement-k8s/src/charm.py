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

import logging
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

    # Adding big comment nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnaaaaaaaaaaaaaaaaaaaaa
    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            WSGIPlacementPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

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
        self.set_readiness_on_related_units()

    def handle_readiness_request_from_event(
        self, event: RelationEvent
    ) -> None:
        """Set service readiness in relation data."""
        self.svc_ready_handler.interface.set_service_status(
            event.relation, self.bootstrapped()
        )

    def set_readiness_on_related_units(self) -> None:
        """Set service readiness on placement related units."""
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
