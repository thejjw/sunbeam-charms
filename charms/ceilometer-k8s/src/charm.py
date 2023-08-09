#!/usr/bin/env python3
"""Ceilometer Operator Charm.

This charm provide Ceilometer services as part of an OpenStack deployment
"""

import logging
import uuid
from typing import List

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as container_handlers
import ops_sunbeam.core as core
from ops.main import main

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


class CeilometerNotificationPebbleHandler(container_handlers.ServicePebbleHandler):
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


class CeilometerOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "ceilometer"
    shared_metering_secret_key = "shared-metering-secret"

    mandatory_relations = {"amqp", "identity-credentials"}

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
    def container_configs(self) -> List[core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = [
            core.ContainerConfigFile(
                "/etc/ceilometer/ceilometer.conf",
                "root",
                "ceilometer",
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
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
            CeilometerNotificationPebbleHandler(
                self,
                CEILOMETER_NOTIFICATION_CONTAINER,
                "ceilometer-notification",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]


if __name__ == "__main__":
    main(CeilometerOperatorCharm)
