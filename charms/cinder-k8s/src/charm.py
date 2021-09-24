#!/usr/bin/env python3
"""Cinder Operator Charm.

This charm provide Cinder services as part of an OpenStack deployment
"""

import logging

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from charms.mysql.v1.mysql import MySQLConsumer

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)

CINDER_API_PORT = 8090


class CinderOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.cinder_api_pebble_ready,
                               self._on_cinder_api_pebble_ready)
        self.framework.observe(self.on.cinder_scheduler_pebble_ready,
                               self._on_cinder_scheduler_pebble_ready)

        self.framework.observe(self.on.config_changed,
                               self._on_config_changed)

        # Register the database consumer and register for events
        self.db = MySQLConsumer(self, 'cinder-db', {"mysql": ">=8"})
        self.framework.observe(self.on.cinder_db_relation_changed,
                               self._on_database_changed)

        # Access to API service from outside of K8S
        self.ingress_public = IngressRequires(self, {
            'service-hostname': self.model.config['os-public-hostname'],
            'service-name': self.app.name,
            'service-port': CINDER_API_PORT,
        })

        self._stored.set_default(db_ready=False)
        self._stored.set_default(amqp_ready=False)
        self._stored.set_default(identity_ready=False)

        # TODO
        # Register AMQP consumer + events
        # Register Identity Service consumer + events

        # TODO
        # State modelling
        # DB & AMQP & Identity -> API and Scheduler
        # Store URL's etc on _changed events?

    @property
    def _pebble_cinder_api_layer(self):
        """Pebble layer for Cinder API"""
        return {
            "summary": "cinder layer",
            "description": "pebble configuration for cinder services",
            "services": {
                "cinder-api": {
                    "override": "replace",
                    "summary": "Cinder API",
                    "command": "/usr/sbin/apache2ctl -DFOREGROUND",
                    "startup": "enabled"
                }
            }
        }

    @property
    def _pebble_cinder_scheduler_layer(self):
        """Pebble layer for Cinder Scheduler"""
        return {
            "summary": "cinder layer",
            "description": "pebble configuration for cinder services",
            "services": {
                "cinder-scheduler": {
                    "override": "replace",
                    "summary": "Cinder Scheduler",
                    "command": "cinder-scheduler --use-syslog",
                    "startup": "enabled"
                }
            }
        }

    def _on_cinder_api_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""
        container = event.workload
        container.add_layer("cinder-api", self._pebble_cinder_api_layer, combine=True)
        container.autostart()

    def _on_cinder_scheduler_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""
        container = event.workload
        container.add_layer("cinder-scheduler", self._pebble_cinder_scheduler_layer, combine=True)
        container.autostart()

    def _on_config_changed(self, _):
        """Just an example to show how to deal with changed configuration"""
        # TODO
        # Set debug logging and restart services
        pass

    def _on_database_ready(self, event):
        """Database ready for use"""
        # TODO
        # Run sync process if on lead unit
        pass

    def _on_amqp_ready(self, event):
        """AMQP service ready for use"""
        pass

    def _on_identity_service_ready(self, event):
        """Identity service ready for use"""
        pass


if __name__ == "__main__":
    main(CinderOperatorCharm)
