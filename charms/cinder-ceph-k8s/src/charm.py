#!/usr/bin/env python3
"""Cinder Ceph Operator Charm.

This charm provide Cinder <-> Ceph integration as part of an OpenStack deployment
"""

import logging

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus

from charms.sunbeam_rabbitmq_operator.v0.amqp import AMQPRequires
from charms.ceph.v0.ceph_client import CephClientRequires

from typing import List

# NOTE: rename sometime
import advanced_sunbeam_openstack.core as core
import advanced_sunbeam_openstack.adapters as adapters

logger = logging.getLogger(__name__)

CINDER_VOLUME_CONTAINER = 'cinder-volume'


class CinderCephAdapters(adapters.OPSRelationAdapters):

    @property
    def interface_map(self):
        _map = super().interface_map
        _map.update({
            'rabbitmq': adapters.AMQPAdapter})
        return _map


class CinderCephOperatorCharm(core.OSBaseOperatorCharm):
    """Cinder/Ceph Operator charm"""

    # NOTE: service_name == container_name
    service_name = 'cinder-volume'

    service_user = 'cinder'
    service_group = 'cinder'

    cinder_conf = '/etc/cinder/cinder.conf'

    def __init__(self, framework):
        super().__init__(
            framework,
            adapters=CinderCephAdapters(self)
        )

    def get_relation_handlers(self) -> List[core.RelationHandler]:
        """Relation handlers for the service."""
        self.amqp = core.AMQPHandler(
            self, "amqp", self.configure_charm
        )
        return [self.amqp]

    @property
    def container_configs(self) -> List[core.ContainerConfigFile]:
        _cconfigs = super().container_configs
        _cconfigs.extend([
            core.ContainerConfigFile(
                [self.service_name],
                self.cinder_conf,
                self.service_user,
                self.service_group
            )
        ])

    def _do_bootstrap(self):
        """No-op the bootstrap method as none required"""
        pass



class CinderCephVictoriaOperatorCharm(CinderCephOperatorCharm):

    openstack_relesae = 'victoria'


if __name__ == "__main__":
    main(CinderCephVictoriaOperatorCharm, use_juju_for_storage=True)
