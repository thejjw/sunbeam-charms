"""CloudCompute Provides and Requires module.


This library contains the Requires and Provides classes for handling
the cloud-compute interface.

Import `CloudComputeRequires` in your charm, with the charm object and the
relation name:
    - self
    - "cloud-compute"

The following events are also available to respond to:
    - connected
    - ready
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.sunbeam_nova_operator.v0.cloud_compute import
CloudComputeRequires

class CloudComputeClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # CloudCompute Requires
        self.cloud_compute = CloudComputeRequires(
            self, "cloud-compute",
            service = "my-service",
            region = "region",
        )
        self.framework.observe(
            self.cloud_compute.on.compute_nodes_connected,
            self._on_cloud_compute_connected)
        self.framework.observe(
            self.cloud_compute.on.compute_nodes_ready,
            self._on_cloud_compute_ready)
        self.framework.observe(
            self.cloud_compute.on.compute_nodes_goneaway,
            self._on_cloud_compute_goneaway)

    def _on_cloud_compute_connected(self, event):
        '''React to the CloudComputeConnectedEvent event.

        This event happens when a CloudCompute relation is added to the
        model before information has been provided
        '''
        # Do something before the relation is complete
        pass

    def _on_cloud_compute_ready(self, event):
        '''React to the CloudComputeReadyEvent event.

        The CloudCompute interface will use the provided config for the
        request to the cloud compute.
        '''
        # CloudCompute Relation is ready. Do something with the completed
        # relation.
        pass

    def _on_cloud_compute_goneaway(self, event):
        '''React to the CloudComputeGoneAwayEvent event.

        This event happens when a CloudCompute relation is removed.
        '''
        # CloudCompute Relation has goneaway. shutdown services or suchlike
        pass
```
"""

# The unique Charmhub library identifier, never change it
import ops.model

# The unique Charmhub library identifier, never change it
LIBID = "44d8650223f143489276f00b1298c2da"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

import logging

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object,
)

from ops.charm import CharmBase
from typing import Union


logger = logging.getLogger(__name__)


class CloudComputeConnectedEvent(EventBase):
    """CloudCompute connected Event."""

    pass


class CloudComputeReadyEvent(EventBase):
    """CloudCompute ready for use Event."""

    def __init__(self, handle, relation_name, relation_id, hostname,
                 availability_zone):
        super().__init__(handle)
        self.relation_name = relation_name
        self.relation_id = relation_id
        self.hostname = hostname
        self.availability_zone = availability_zone

    def snapshot(self):
        return {
            'relation_name': self.relation_name,
            'relation_id': self.relation_id,
            'hostname': self.hostname,
            'availability_zone': self.availability_zone,
        }

    def restore(self, snapshot):
        super().restore(snapshot)
        self.relation_name = snapshot['relation_name']
        self.relation_id = snapshot['relation_id']
        self.hostname = snapshot['hostname']
        self.availability_zone = snapshot['availability_zone']


class CloudComputeGoneAwayEvent(EventBase):
    """CloudCompute relation has gone-away Event"""

    pass


class CloudComputeEvents(ObjectEvents):
    """Events class for `on`"""

    compute_nodes_connected = EventSource(CloudComputeConnectedEvent)
    compute_nodes_ready = EventSource(CloudComputeReadyEvent)
    compute_nodes_goneaway = EventSource(CloudComputeGoneAwayEvent)


class CloudComputeRequires(Object):
    """
    CloudComputeRequires class
    """

    on = CloudComputeEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_cloud_compute_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_cloud_compute_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_cloud_compute_relation_broken,
        )

    def _on_cloud_compute_relation_changed(self, event):
        """CloudCompute relation changed."""
        logger.debug('cloud-compute requires on_changed')
        try:
            unit_relation_data = event.relation.data[event.unit]
            hostname = unit_relation_data.get('hostname')
            availability_zone = unit_relation_data.get('availability_zone')

            if not hostname or not availability_zone:
                logger.debug('Missing hostname or availability zone. Waiting '
                             'to raise event until ready')
                return

            # TODO(wolsen) Need to get the migration auth type and credentials.
            self.on.compute_nodes_ready.emit(
                event.relation.name,
                event.relation.id,
                hostname,
                availability_zone,
            )
        except AttributeError:
            logger.exception('Error when emitting event.')
            raise

    def _on_cloud_compute_relation_broken(self, event):
        """CloudCompute relation broken."""
        logging.debug("CloudCompute on_broken")
        self.on.compute_nodes_goneaway.emit()

    def set_controller_info(
            self, region: str, cross_az_attach: bool = False,
            volume_service: str = 'cinder', network_manager: str = 'neutron',
    ) -> None:
        """Set controller information for the compute-nodes."""
        if not self.model.unit.is_leader():
            logging.debug('Not leader, leader will send information')
            return

        logging.debug('Broadcasting controller information to all '
                      f'{self.relation_name} relations.')
        for relation in self.framework.model.relations.get(self.relation_name):
            app_data = relation.data[self.charm.app]
            app_data['network-manager'] = network_manager
            app_data['region'] = region
            app_data['cross-az-attach'] = str(cross_az_attach)
            app_data['volume-service'] = volume_service


class HasCloudComputeClientsEvent(EventBase):
    """Has CloudComputeClients Event."""

    def __init__(self, handle, relation_name, relation_id):
        super().__init__(handle)
        self.relation_name = relation_name
        self.relation_id = relation_id

    def snapshot(self):
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
        }

    def restore(self, snapshot):
        super().restore(snapshot)
        self.relation_name = snapshot["relation_name"]
        self.relation_id = snapshot["relation_id"]


class ReadyCloudComputeClientsEvent(EventBase):
    """CloudComputeClients Ready Event."""

    def __init__(self, handle, relation_name, relation_id):
        super().__init__(handle)
        self.relation_name = relation_name
        self.relation_id = relation_id

    def snapshot(self):
        return {
            "relation_name": self.relation_name,
            "relation_id": self.relation_id,
        }

    def restore(self, snapshot):
        super().restore(snapshot)
        self.relation_name = snapshot["relation_name"]
        self.relation_id = snapshot["relation_id"]


class CloudComputeClientsGoneAway(EventBase):
    """CloudComputeClients gone away Event."""

    pass


class CloudComputeClientEvents(ObjectEvents):
    """Events class for `on`"""

    has_cloud_compute_clients = EventSource(HasCloudComputeClientsEvent)
    ready_cloud_compute_clients = EventSource(ReadyCloudComputeClientsEvent)
    cloud_compute_clients_gone = EventSource(CloudComputeClientsGoneAway)


class CloudComputeProvides(Object):
    """
    CloudComputeProvides class
    """

    on = CloudComputeClientEvents()
    _stored = StoredState()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_cloud_compute_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_cloud_compute_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_cloud_compute_relation_broken,
        )

    def _on_cloud_compute_relation_joined(self, event):
        """Handle CloudCompute joined."""
        logging.debug(f'cloud-compute joined event for {event.relation.name},'
                      f' {event.relation.id}')
        self.on.has_cloud_compute_clients.emit(
            event.relation.name,
            event.relation.id,
        )

    def _on_cloud_compute_relation_changed(self, event):
        """Handle CloudCompute changed."""
        logging.debug("cloud-compute on_changed")
        self.on.ready_cloud_compute_clients.emit(
            event.relation.name,
            event.relation.id,
        )

    def _on_cloud_compute_relation_broken(self, event):
        """Handle CloudCompute broken."""
        logging.debug("CloudComputeProvides on_departed")
        self.on.cloud_compute_clients_gone.emit()

    def set_compute_node_info(self, relation_name: int, relation_id: str,
                              hostname: str, availability_zone: str):
        logging.debug(f"Setting compute node information for {relation_name},"
                      f" {relation_id}")
        relation = self.framework.model.get_relation(relation_name,
                                                     relation_id)

        unit_data = relation.data[self.charm.unit]
        unit_data['hostname'] = hostname
        unit_data['availability_zone'] = availability_zone

    @property
    def _cloud_compute_rel(self) -> ops.model.Relation:
        return self.framework.model.get_relation(self.relation_name)

    def _get_remote_app_data(self, key: str) -> Union[str, bool, int, None]:
        relation = self._cloud_compute_rel
        data = relation.data[relation.app]
        return data.get(key)

    @property
    def network_manager(self):
        return self._get_remote_app_data('network-manager')

    @property
    def volume_service(self):
        return self._get_remote_app_data('volume-service')

    @property
    def region(self):
        return self._get_remote_app_data('region')

    @property
    def cross_az_attach(self):
        return self._get_remote_app_data('cross-az-attach')
