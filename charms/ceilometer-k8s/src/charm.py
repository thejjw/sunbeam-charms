#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
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

"""Ceilometer Operator Charm.

This charm provide Ceilometer services as part of an OpenStack deployment
"""

import logging
import uuid
from typing import (
    Callable,
    List,
)

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.ceilometer_k8s.v0.ceilometer_service import (
    CeilometerConfigRequestEvent,
    CeilometerServiceProvides,
)
from ops.charm import (
    ActionEvent,
    CharmBase,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

CEILOMETER_CENTRAL_CONTAINER = "ceilometer-central"
CEILOMETER_NOTIFICATION_CONTAINER = "ceilometer-notification"


class CeilometerServiceProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for ceilometer service relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for an Ceilometer service relation."""
        logger.debug("Setting up Ceilometer service event handler")
        svc = CeilometerServiceProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_request,
            self._on_config_request,
        )
        return svc

    def _on_config_request(self, event: CeilometerConfigRequestEvent) -> None:
        """Handle Config request event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


class CeilometerCentralPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for ceilometer-central service."""

    def get_layer(self) -> dict:
        """ceilometer-central service pebble layer.

        :returns: pebble layer configuration for ceilometer-central service
        :rtype: dict
        """
        return {
            "summary": "ceilometer-central layer",
            "description": "pebble config layer for ceilometer-central service",
            "services": {
                "ceilometer-central": {
                    "override": "replace",
                    "summary": "ceilometer-central",
                    "command": "/usr/bin/ceilometer-polling --config-file=/etc/ceilometer/ceilometer.conf --polling-namespaces central --use-syslog",
                    "startup": "enabled",
                    "user": "ceilometer",
                    "group": "ceilometer",
                },
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler.

        :returns: Container configuration files
        :rtype: List[ContainerConfigFile]
        """
        return self.charm.container_configs


class CeilometerNotificationPebbleHandler(
    sunbeam_chandlers.ServicePebbleHandler
):
    """Pebble handler for ceilometer-notification service."""

    def get_layer(self) -> dict:
        """ceilometer-notification service pebble layer.

        :returns: pebble layer configuration for ceilometer-notification service
        :rtype: dict
        """
        return {
            "summary": "ceilometer-notification layer",
            "description": "pebble config layer for ceilometer-notification service",
            "services": {
                "ceilometer-notification": {
                    "override": "replace",
                    "summary": "ceilometer-notification",
                    "command": "/usr/bin/ceilometer-agent-notification --config-file=/etc/ceilometer/ceilometer.conf --use-syslog",
                    "startup": "enabled",
                    "user": "ceilometer",
                    "group": "ceilometer",
                },
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler.

        :returns: Container configuration files
        :rtype: List[ContainerConfigFile]
        """
        _cconfigs = self.charm.container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/ceilometer/pipeline.yaml",
                    self.charm.service_user,
                    self.charm.service_group,
                    0o640,
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/ceilometer/event_pipeline.yaml",
                    self.charm.service_user,
                    self.charm.service_group,
                    0o640,
                ),
            ]
        )
        return _cconfigs


class CeilometerOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "ceilometer"
    shared_metering_secret_key = "shared-metering-secret"

    mandatory_relations = {"amqp", "identity-credentials"}

    def __init__(self, framework: ops.framework):
        super().__init__(framework)
        self.framework.observe(
            self.on.ceilometer_upgrade_action, self._ceilometer_upgrade_action
        )

    def get_shared_meteringsecret(self):
        """Return the shared metering secret."""
        return self.leader_get(self.shared_metering_secret_key)

    def set_shared_meteringsecret(self):
        """Store the shared metering secret."""
        self.leader_set({self.shared_metering_secret_key: str(uuid.uuid1())})

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Callback handler for nova operator configuration."""
        if not self.peers.ready:
            return
        metering_secret = self.get_shared_meteringsecret()
        if metering_secret:
            logger.debug("Found metering secret in leader DB")
        else:
            if self.unit.is_leader():
                logger.debug("Creating metering secret")
                self.set_shared_meteringsecret()
                self.set_config_on_update()
            else:
                logger.debug("Metadata secret not ready")
                return
        super().configure_charm(event)

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "ceilometer"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "ceilometer"

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/ceilometer/ceilometer.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
        ]
        return _cconfigs

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the operator."""
        return [
            CeilometerCentralPebbleHandler(
                self,
                CEILOMETER_CENTRAL_CONTAINER,
                "ceilometer-central",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            CeilometerNotificationPebbleHandler(
                self,
                CEILOMETER_NOTIFICATION_CONTAINER,
                "ceilometer-notification",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("ceilometer-service", handlers):
            self.config_svc = CeilometerServiceProvidesHandler(
                self,
                "ceilometer-service",
                self.set_config_from_event,
            )
            handlers.append(self.config_svc)

        return super().get_relation_handlers(handlers)

    def set_config_from_event(self, event: ops.framework.EventBase) -> None:
        """Set config in relation data."""
        telemetry_secret = self.get_shared_meteringsecret()
        if telemetry_secret:
            self.config_svc.interface.set_config(
                relation=event.relation, telemetry_secret=telemetry_secret
            )
        else:
            logging.debug("Telemetry secret not yet set, not sending config")

    def set_config_on_update(self) -> None:
        """Set config on relation on update of local data."""
        telemetry_secret = self.get_shared_meteringsecret()
        if telemetry_secret:
            self.config_svc.interface.set_config(
                relation=None, telemetry_secret=telemetry_secret
            )
        else:
            logging.debug("Telemetry secret not yet set, not sending config")

    def _ceilometer_upgrade_action(self, event: ActionEvent) -> None:
        """Run ceilometer-upgrade.

        This action will upgrade the data store configuration in gnocchi.
        """
        try:
            logger.info("Syncing database...")
            cmd = ["ceilometer-upgrade"]
            container = self.unit.get_container(
                CEILOMETER_NOTIFICATION_CONTAINER
            )
            process = container.exec(cmd, timeout=5 * 60)
            out, warnings = process.wait_output()
            logging.debug("Output from database sync: \n%s", out)
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("DB Sync Out: %s", line.strip())
                event.fail(f"Error in running ceilometer-upgrade: {warnings}")
            else:
                event.set_results({"message": "ceilometer-upgrade successful"})
        except Exception as e:
            logger.exception(e)
            event.fail(f"Error in running ceilometer-updgrade: {e}")


if __name__ == "__main__":
    main(CeilometerOperatorCharm)
