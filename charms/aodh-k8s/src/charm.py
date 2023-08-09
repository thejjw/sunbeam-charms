#!/usr/bin/env python3
"""Aodh Operator Charm.

This charm provide Aodh services as part of an OpenStack deployment
"""

import logging
from typing import List

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
from ops.framework import StoredState
from ops.main import main

logger = logging.getLogger(__name__)

AODH_WSGI_CONTAINER = "aodh-api"
AODH_EVALUATOR_CONTAINER = "aodh-evaluator"
AODH_NOTIFIER_CONTAINER = "aodh-notifier"
AODH_LISTENER_CONTAINER = "aodh-listener"
AODH_EXPIRER_CONTAINER = "aodh-expirer"


class AODHEvaluatorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for AODH Evaluator."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_layer(self) -> dict:
        """AODH Evaluator service layer.

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "aodh evaluator layer",
            "description": "pebble configuration for aodh-evaluator service",
            "services": {
                "aodh-evaluator": {
                    "override": "replace",
                    "summary": "AODH Evaluator",
                    "command": "aodh-evaluator",
                    "startup": "enabled",
                    "user": "aodh",
                    "group": "aodh",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/aodh/aodh.conf",
                "root",
                "aodh",
                0o640,
            )
        ]


class AODHNotifierPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for AODH Notifier container."""

    def get_layer(self):
        """AODH Notifier service.

        :returns: pebble service layer configuration for aodh-notifier service
        :rtype: dict
        """
        return {
            "summary": "aodh notifier layer",
            "description": "pebble configuration for aodh-notifier service",
            "services": {
                "aodh-notifier": {
                    "override": "replace",
                    "summary": "AODH Notifier",
                    "command": "aodh-notifier",
                    "startup": "enabled",
                    "user": "aodh",
                    "group": "aodh",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/aodh/aodh.conf",
                "root",
                "aodh",
                0o640,
            )
        ]


class AODHListenerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for AODH Listener container."""

    def get_layer(self):
        """AODH Listener service.

        :returns: pebble service layer configuration for aodh-listener service
        :rtype: dict
        """
        return {
            "summary": "aodh listener layer",
            "description": "pebble configuration for AODH Listener service",
            "services": {
                "aodh-listener": {
                    "override": "replace",
                    "summary": "AODH Listener",
                    "command": "aodh-listener",
                    "startup": "enabled",
                    "user": "aodh",
                    "group": "aodh",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/aodh/aodh.conf",
                "root",
                "aodh",
                0o640,
            )
        ]


class AODHExpirerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for AODH Expirer container."""

    def get_layer(self):
        """AODH Expirer service.

        :returns: pebble service layer configuration for aodh-expirer service
        :rtype: dict
        """
        return {
            "summary": "aodh expirer layer",
            "description": "pebble configuration for AODH Expirer service",
            "services": {
                "aodh-expirer": {
                    "override": "replace",
                    "summary": "AODH Expirer",
                    "command": ('/bin/bash -c "while true; do   aodh-expirer; sleep 60; done"'),
                    "startup": "enabled",
                    "user": "aodh",
                    "group": "aodh",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/aodh/aodh.conf",
                "root",
                "aodh",
                0o640,
            )
        ]


class AodhOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "aodh-api"
    wsgi_admin_script = "/usr/share/aodh/app.wsgi"
    wsgi_public_script = "/usr/share/aodh/app.wsgi"

    db_sync_cmds = [["aodh-dbsync"]]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/aodh/aodh.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "aodh"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "aodh"

    @property
    def service_endpoints(self):
        """Return service endpoints for the service."""
        return [
            {
                "service_name": "aodh",
                "type": "aodh",
                "description": "OpenStack Aodh API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 8042

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        #        if self.config.get("alarm-history-time-to-live") > 0:
        #            enable_expirer = True
        #        else:
        #            enable_expirer = False
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                AODH_WSGI_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            AODHEvaluatorPebbleHandler(
                self,
                AODH_EVALUATOR_CONTAINER,
                "aodh-evaluator",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            AODHNotifierPebbleHandler(
                self,
                AODH_NOTIFIER_CONTAINER,
                "aodh-notifier",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            AODHListenerPebbleHandler(
                self,
                AODH_LISTENER_CONTAINER,
                "aodh-listener",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            AODHExpirerPebbleHandler(
                self,
                AODH_EXPIRER_CONTAINER,
                "aodh-expirer",
                [],
                self.template_dir,
                self.configure_charm,
                #                enable_expirer,
            ),
        ]
        return pebble_handlers


if __name__ == "__main__":
    main(AodhOperatorCharm)
