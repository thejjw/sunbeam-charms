#!/usr/bin/env python3
"""Cinder Ceph Operator Charm.

This charm provide Cinder <-> Ceph integration as part of an OpenStack deployment
"""

import logging


from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus

logger = logging.getLogger(__name__)

CINDER_API_PORT = 8090


class CinderCephOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.cinder_volume_pebble_ready,
                               self._on_cinder_volume_pebble_ready)

        self.framework.observe(self.on.config_changed,
                               self._on_config_changed)

        self._stored.set_default(amqp_ready=False)
        self._stored.set_default(ceph_ready=False)

        # TODO
        # Register AMQP consumer + events

        # TODO
        # State modelling
        # AMQP + Ceph -> +Volume

    @property
    def _pebble_cinder_volume_layer(self):
        """Pebble layer for Cinder volume"""
        return {
            "summary": "cinder layer",
            "description": "pebble configuration for cinder services",
            "services": {
                "cinder-volume": {
                    "override": "replace",
                    "summary": "Cinder Volume",
                    "command": "cinder-volume --use-syslog",
                    "startup": "enabled"
                }
            }
        }

    def _on_cinder_volume_pebble_ready(self, event):
        """Define and start a workload using the Pebble API."""
        container = event.workload
        container.add_layer("cinder-volume", self._pebble_cinder_scheduler_layer, combine=True)
        container.autostart()

    def _on_config_changed(self, _):
        """Just an example to show how to deal with changed configuration"""
        # TODO
        # Set debug logging and restart services
        pass

    def _on_amqp_ready(self, event):
        """AMQP service ready for use"""
        pass


if __name__ == "__main__":
    main(CinderCephOperatorCharm)
