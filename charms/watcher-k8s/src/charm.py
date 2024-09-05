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
"""Watcher Operator Charm.

This charm provide watcher services as part of an OpenStack deployment
"""

import logging

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)
WATCHER_API_CONTAINER = "watcher-api"
WATCHER_DECISION_ENGINE_CONTAINER = "watcher-decision-engine"
WATCHER_APPLIER_CONTAINER = "watcher-applier"


@sunbeam_tracing.trace_type
class WatcherDecisionEnginePebbleHandler(
    sunbeam_chandlers.ServicePebbleHandler
):
    """Pebble handler for Watcher decision engine."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Watcher decision engine service layer.

        :returns: pebble layer configuration for watcher decision engine service
        :rtype: dict
        """
        return {
            "summary": "watcher decision engine layer",
            "description": "pebble configuration for watcher services",
            "services": {
                "watcher-decision-engine": {
                    "override": "replace",
                    "summary": "Watcher decision engine",
                    "command": "watcher-decision-engine",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logger.debug("Service checks enabled for watcher decision engine")
            return super().service_ready
        else:
            logger.debug("Service checks disabled for watcher decision engine")
            return self.pebble_ready


@sunbeam_tracing.trace_type
class WatcherApplierPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Watcher applier."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Watcher applier service layer.

        :returns: pebble layer configuration for watcher applier service
        :rtype: dict
        """
        return {
            "summary": "watcher applier layer",
            "description": "pebble configuration for watcher services",
            "services": {
                "watcher-applier": {
                    "override": "replace",
                    "summary": "Watcher applier",
                    "command": "watcher-applier",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logger.debug("Service checks enabled for watcher applier")
            return super().service_ready
        else:
            logger.debug("Service checks disabled for watcher applier")
            return self.pebble_ready


class WatcherOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    service_name = "watcher-api"
    wsgi_admin_script = "/usr/bin/watcher-api-wsgi"
    wsgi_public_script = "/usr/bin/watcher-api-wsgi"
    mandatory_relations = {"database", "amqp", "identity-service"}

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "watcher",
            "watcher-db-manage",
            "upgrade",
        ]
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/watcher/watcher.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "watcher"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "watcher"

    @property
    def service_endpoints(self):
        """Service endpoints configuration."""
        return [
            {
                "service_name": "watcher",
                "type": "infra-optim",
                "description": "Infrastructure Optimization",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default port."""
        return 9322

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container config files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    self.service_conf,
                    self.service_user,
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
        )

        return _cconfigs

    def get_pebble_handlers(
        self,
    ) -> list[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = super().get_pebble_handlers()
        pebble_handlers.extend(
            [
                WatcherDecisionEnginePebbleHandler(
                    self,
                    WATCHER_DECISION_ENGINE_CONTAINER,
                    "watcher-decision-engine",
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                ),
                WatcherApplierPebbleHandler(
                    self,
                    WATCHER_APPLIER_CONTAINER,
                    "watcher-applier",
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                ),
            ]
        )
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("gnocchi-db", handlers):
            self.gnocchi_svc = sunbeam_rhandlers.GnocchiServiceRequiresHandler(
                self,
                "gnocchi-db",
                self.configure_charm,
                "gnocchi-db" in self.mandatory_relations,
            )
            handlers.append(self.gnocchi_svc)

        return super().get_relation_handlers(handlers)

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Callback handler for watcher operator configuration."""
        # Do not run service check for watcher decision engine and watcher
        # applier as it is broken until db migrations have run.
        decision_engine_handler = self.get_named_pebble_handler(
            WATCHER_DECISION_ENGINE_CONTAINER
        )
        decision_engine_handler.enable_service_check = False
        applier_handler = self.get_named_pebble_handler(
            WATCHER_APPLIER_CONTAINER
        )
        applier_handler.enable_service_check = False

        super().configure_charm(event)
        if decision_engine_handler.pebble_ready:
            logger.debug(
                "Starting watcher decision engine service, pebble ready"
            )
            # Restart watcher-decision-engine service
            decision_engine_handler.start_all()
            decision_engine_handler.enable_service_check = True
        else:
            logger.debug(
                "Not starting watcher decision engine service, pebble not ready"
            )
        if applier_handler.pebble_ready:
            logger.debug("Starting watcher applier service, pebble ready")
            # Restart watcher-applier service
            applier_handler.start_all()
            applier_handler.enable_service_check = True
        else:
            logger.debug(
                "Not starting watcher applier service, pebble not ready"
            )


if __name__ == "__main__":  # pragma: nocover
    ops.main(WatcherOperatorCharm)  # type: ignore
