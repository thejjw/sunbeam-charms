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

"""Aodh Operator Charm.

This charm provide Aodh services as part of an OpenStack deployment
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
import ops_sunbeam.tracing as sunbeam_tracing
from ops.framework import (
    StoredState,
)

logger = logging.getLogger(__name__)

AODH_WSGI_CONTAINER = "aodh-api"
AODH_EVALUATOR_CONTAINER = "aodh-evaluator"
AODH_NOTIFIER_CONTAINER = "aodh-notifier"
AODH_LISTENER_CONTAINER = "aodh-listener"
AODH_EXPIRER_CONTAINER = "aodh-expirer"


@sunbeam_tracing.trace_type
class AODHWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for AODH api service."""

    def init_service(self, context) -> None:
        """Initialise the container."""
        try:
            self.execute(["a2dissite", "aodh-api"], exception_on_error=True)
        except ops.pebble.ExecError:
            logger.exception("Failed to disable aodh-api site in apache")

        super().init_service(context)


@sunbeam_tracing.trace_type
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
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "aodh",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
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
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "aodh",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
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
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "aodh",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
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
                    "command": (
                        '/bin/bash -c "while true; do   aodh-expirer; sleep 86400; done"'
                    ),
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
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "aodh",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_sunbeam_charm
class AodhOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "aodh-api"
    wsgi_admin_script = "/usr/share/aodh/app.wsgi"
    wsgi_public_script = "/usr/share/aodh/app.wsgi"

    db_sync_cmds = [["aodh-dbsync"]]

    mandatory_relations = {
        "database",
        "identity-service",
        "ingress-public",
        "amqp",
    }

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
                "type": "alarming",
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
            AODHWSGIPebbleHandler(
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


if __name__ == "__main__":  # pragma: nocover
    ops.main(AodhOperatorCharm)
