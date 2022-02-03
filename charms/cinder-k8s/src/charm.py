#!/usr/bin/env python3
"""Cinder Operator Charm.

This charm provide Cinder services as part of an OpenStack deployment
"""

import logging
from typing import List

import ops.pebble

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.container_handlers as sunbeam_chandlers
import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers

import charms.sunbeam_cinder_operator.v0.storage_backend as sunbeam_storage_backend  # noqa

logger = logging.getLogger(__name__)

CINDER_API_PORT = 8090
CINDER_API_CONTAINER = "cinder-api"
CINDER_SCHEDULER_CONTAINER = "cinder-scheduler"


class CinderWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    def start_service(self):
        pass

    def init_service(self, context) -> None:
        """Enable and start WSGI service"""
        self.write_config(context)
        try:
            self.execute(
                [
                    "a2disconf",
                    "cinder-wsgi"
                ],
                exception_on_error=True
            )
            self.execute(
                [
                    "a2ensite",
                    self.wsgi_service_name
                ],
                exception_on_error=True
            )
        except ops.pebble.ExecError:
            logger.exception(
                f"Failed to enable {self.wsgi_service_name} site in apache"
            )
            # ignore for now - pebble is raising an exited too quickly, but it
            # appears to work properly.
        self.start_wsgi()
        self._state.service_ready = True


class CinderSchedulerPebbleHandler(sunbeam_chandlers.PebbleHandler):
    def start_service(self):
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

    def get_layer(self):
        """Apache service

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

    def init_service(self, context):
        self.write_config(context)
        self.start_service()
        self._state.service_ready = True

    def default_container_configs(self):
        return [
            sunbeam_core.ContainerConfigFile(
                [self.container_name],
                "/etc/cinder/cinder.conf",
                "cinder",
                "cinder",
            )
        ]


class StorageBackendRequiresHandler(sunbeam_rhandlers.RelationHandler):
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
        return self.interface.set_ready()

    @property
    def ready(self) -> bool:
        return True


class CinderOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    _authed = False
    service_name = "cinder"
    wsgi_admin_script = "/usr/bin/cinder-wsgi-admin"
    wsgi_public_script = "/usr/bin/cinder-wsgi-public"

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
                self, "storage-backend", self.configure_charm
            )
            handlers.append(self.sb_svc)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    @property
    def service_endpoints(self):
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

    def get_pebble_handlers(self):
        pebble_handlers = [
            CinderWSGIPebbleHandler(
                self,
                CINDER_API_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            CinderSchedulerPebbleHandler(
                self,
                CINDER_SCHEDULER_CONTAINER,
                "cinder-scheduler",
                [],
                self.template_dir,
                self.openstack_release,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def default_public_ingress_port(self):
        return 8776

    @property
    def wsgi_container_name(self):
        return CINDER_API_CONTAINER

    def _do_bootstrap(self):
        """
        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
        """
        super()._do_bootstrap()
        try:
            logger.info("Syncing database...")
            pebble_handler = self.charm.get_named_pebble_handler(
                CINDER_SCHEDULER_CONTAINER
            )
            pebble_handler.execute([
                "sudo",
                "-u",
                "cinder",
                "cinder-manage",
                "--config-dir",
                "/etc/cinder",
                "db",
                "sync"],
                exception_on_error=True
            )
        except ops.pebble.ExecError:
            logger.exception("Failed to bootstrap")
            self._state.bootstrapped = False
            return

    def configure_charm(self, event) -> None:
        super().configure_charm(event)
        # Restarting services after bootstrap should be in aso
        if self._state.bootstrapped:
            for handler in self.pebble_handlers:
                handler.start_service()
            # Tell storage backends we are ready
            self.sb_svc.set_ready()


class CinderWallabyOperatorCharm(CinderOperatorCharm):

    openstack_release = "wallaby"


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(CinderWallabyOperatorCharm, use_juju_for_storage=True)
