#!/usr/bin/env python3

#
# Copyright 2021 Canonical Ltd.
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


"""Cinder Operator Charm.

This charm provide Cinder services as part of an OpenStack deployment
"""

import logging
from typing import (
    Dict,
    List,
)

import charms.cinder_k8s.v0.storage_backend as sunbeam_storage_backend  # noqa
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

CINDER_API_PORT = 8090
CINDER_API_CONTAINER = "cinder-api"
CINDER_SCHEDULER_CONTAINER = "cinder-scheduler"


class CinderWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for Cinder WSGI services."""

    def start_service(self):
        """Start services in container."""
        pass

    def init_service(self, context) -> None:
        """Enable and start WSGI service."""
        self.write_config(context)
        try:
            self.execute(["a2disconf", "cinder-wsgi"], exception_on_error=True)
            self.execute(
                ["a2ensite", self.wsgi_service_name], exception_on_error=True
            )
        except ops.pebble.ExecError:
            logger.exception(
                f"Failed to enable {self.wsgi_service_name} site in apache"
            )
            # ignore for now - pebble is raising an exited too quickly, but it
            # appears to work properly.
        self.start_wsgi()
        self._state.service_ready = True

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for cinder-api
                  service
        :rtype: dict
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "exec": {"command": "service apache2 status"},
                },
            }
        }


class CinderSchedulerPebbleHandler(sunbeam_chandlers.PebbleHandler):
    """Pebble handler for Cinder Scheduler services."""

    def start_service(self):
        """Start services in container."""
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(
                f"{self.container_name} container is not ready. "
                "Cannot start service."
            )
            return
        service = container.get_service(self.service_name)
        if service.is_running():
            container.stop(self.service_name)

        container.start(self.service_name)

    def get_layer(self) -> dict:
        """Cinder Scheduler service.

        :returns: pebble layer configuration for wsgi services
        :rtype: dict
        """
        return {
            "summary": "cinder layer",
            "description": "pebble configuration for cinder services",
            "services": {
                "cinder-scheduler": {
                    "override": "replace",
                    "summary": "Cinder Scheduler",
                    "command": "cinder-scheduler --use-syslog",
                    "startup": "enabled",
                }
            },
        }

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "exec": {"command": "service cinder-scheduler status"},
                },
            }
        }

    def init_service(self, context) -> None:
        """Initialize services and write configuration."""
        self.write_config(context)
        self.start_service()

    def default_container_configs(self) -> List[Dict]:
        """Generate default configuration files for container."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/cinder/cinder.conf",
                "cinder",
                "cinder",
            )
        ]


class StorageBackendRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for cinder storage backends."""

    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        sb_svc = sunbeam_storage_backend.StorageBackendRequires(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(sb_svc.on.ready, self._on_ready)
        return sb_svc

    def _on_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    def set_ready(self) -> None:
        """Flag that all services are running and ready for use."""
        return self.interface.set_ready()

    @property
    def ready(self) -> bool:
        """Determine whether interface is ready for use."""
        return True


class CinderOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    _authed = False
    service_name = "cinder"
    wsgi_admin_script = "/usr/bin/cinder-wsgi"
    wsgi_public_script = "/usr/bin/cinder-wsgi"

    mandatory_relations = {
        "database",
        "amqp",
        "storage-backend",
        "identity-service",
        "ingress-public",
    }

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(admin_domain_name="admin_domain")
        self._state.set_default(admin_domain_id=None)
        self._state.set_default(default_domain_id=None)
        self._state.set_default(service_project_id=None)

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("storage-backend", handlers):
            self.sb_svc = StorageBackendRequiresHandler(
                self,
                "storage-backend",
                self.configure_charm,
                "storage-backend" in self.mandatory_relations,
            )
            handlers.append(self.sb_svc)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    @property
    def service_endpoints(self) -> List[Dict]:
        """Service endpoints for the Cinder API services."""
        return [
            {
                "service_name": "cinderv2",
                "type": "volumev2",
                "description": "Cinder Volume Service v2",
                "internal_url": f"{self.internal_url}/v2/$(tenant_id)s",
                "public_url": f"{self.public_url}/v2/$(tenant_id)s",
                "admin_url": f"{self.admin_url}/v2/$(tenant_id)s",
            },
            {
                "service_name": "cinderv3",
                "type": "volumev3",
                "description": "Cinder Volume Service v3",
                "internal_url": f"{self.internal_url}/v3/$(tenant_id)s",
                "public_url": f"{self.public_url}/v3/$(tenant_id)s",
                "admin_url": f"{self.admin_url}/v3/$(tenant_id)s",
            },
        ]

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/cinder/api-paste.ini",
                    self.service_user,
                    self.service_group,
                )
            ]
        )
        return _cconfigs

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the charm."""
        pebble_handlers = [
            CinderWSGIPebbleHandler(
                self,
                CINDER_API_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            CinderSchedulerPebbleHandler(
                self,
                CINDER_SCHEDULER_CONTAINER,
                "cinder-scheduler",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def default_public_ingress_port(self):
        """Public ingress port for service."""
        return 8776

    @property
    def wsgi_container_name(self):
        """WSGI API service container name."""
        return CINDER_API_CONTAINER

    def _do_bootstrap(self):
        """Bootstrap the service ready for use.

        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
        """
        super()._do_bootstrap()
        try:
            logger.info("Syncing database...")
            pebble_handler = self.get_named_pebble_handler(
                CINDER_SCHEDULER_CONTAINER
            )
            pebble_handler.execute(
                [
                    "sudo",
                    "-u",
                    "cinder",
                    "cinder-manage",
                    "--config-dir",
                    "/etc/cinder",
                    "db",
                    "sync",
                ],
                exception_on_error=True,
            )
        except ops.pebble.ExecError:
            logger.exception("Failed to bootstrap")
            self._state.bootstrapped = False
            return

    def configure_charm(self, event) -> None:
        """Configure the charmed services."""
        super().configure_charm(event)
        # Restarting services after bootstrap should be in aso
        if self._state.bootstrapped:
            for handler in self.pebble_handlers:
                handler.start_service()
            # Tell storage backends we are ready
            self.sb_svc.set_ready()


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(CinderOperatorCharm, use_juju_for_storage=True)
