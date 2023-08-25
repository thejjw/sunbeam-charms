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
    List,
)

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as container_handlers
import ops_sunbeam.core as sunbeam_core
from ops.charm import (
    ActionEvent,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

CEILOMETER_CENTRAL_CONTAINER = "ceilometer-central"
CEILOMETER_NOTIFICATION_CONTAINER = "ceilometer-notification"


class CeilometerCentralPebbleHandler(container_handlers.ServicePebbleHandler):
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
    container_handlers.ServicePebbleHandler
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

    def get_pebble_handlers(self) -> List[container_handlers.PebbleHandler]:
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
