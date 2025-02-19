"""CinderVolume Provides and Requires module.

This library contains the Requires and Provides classes for handling
the cinder-volume interface.

Import `CinderVolumeRequires` in your charm, with the charm object and the
relation name:
    - self
    - "cinder-volume"
    - backend_key

Three events are also available to respond to:
    - connected
    - ready
    - goneaway

A basic example showing the usage of this relation follows:


```
from charms.cinder_volume.v0.cinder_volume import CinderVolumeRequires

class CinderVolumeDriver(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # CinderVolume Requires
        self.cinder_volume = CinderVolumeRequires(
            self,
            relation_name="cinder-volume",
            backend_key="ceph.monoceph",
        )
        self.framework.observe(
            self.cinder_volume.on.connected, self._on_cinder_volume_connected
        )
        self.framework.observe(
            self.cinder_volume.on.ready, self._on_cinder_volume_ready
        )
        self.framework.observe(
            self.cinder_volume.on.goneaway, self._on_cinder_volume_goneaway
        )

    def _on_cinder_volume_connected(self, event):
        '''React to the CinderVolume connected event.

        This event happens when CinderVolume relation is added to the
        model before credentials etc have been provided.
        '''
        # Do something before the relation is complete
        pass

    def _on_cinder_volume_ready(self, event):
        '''React to the CinderVolume ready event.

        This event happens when an CinderVolume relation is ready.
        '''
        # CinderVolume Relation is ready. Configure services or suchlike
        pass

    def _on_cinder_volume_goneaway(self, event):
        '''React to the CinderVolume goneaway event.

        This event happens when an CinderVolume relation is broken.
        '''
        # CinderVolume Relation has goneaway. shutdown services or suchlike
        pass

```
"""

import logging

import ops


# The unique Charmhub library identifier, never change it
LIBID = "9aa142db811f4f8588a257d7dc6dff86"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)

BACKEND_KEY = "backend"
SNAP_KEY = "snap-name"


class CinderVolumeConnectedEvent(ops.RelationJoinedEvent):
    """CinderVolume connected Event."""

    pass


class CinderVolumeReadyEvent(ops.RelationChangedEvent):
    """CinderVolume ready for use Event."""

    pass


class CinderVolumeGoneAwayEvent(ops.RelationBrokenEvent):
    """CinderVolume relation has gone-away Event"""

    pass


class CinderVolumeRequiresEvents(ops.ObjectEvents):
    """Events class for `on`"""

    connected = ops.EventSource(CinderVolumeConnectedEvent)
    ready = ops.EventSource(CinderVolumeReadyEvent)
    goneaway = ops.EventSource(CinderVolumeGoneAwayEvent)


def remote_unit(relation: ops.Relation) -> ops.Unit | None:
    if len(relation.units) == 0:
        return None
    return list(relation.units)[0]


class CinderVolumeRequires(ops.Object):
    """
    CinderVolumeRequires class
    """

    on = CinderVolumeRequiresEvents()  # type: ignore

    def __init__(
        self, charm: ops.CharmBase, relation_name: str, backend_key: str
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.backend_key = backend_key
        rel_observer = self.charm.on[relation_name]
        self.framework.observe(
            rel_observer.relation_joined,
            self._on_cinder_volume_relation_joined,
        )
        self.framework.observe(
            rel_observer.relation_changed,
            self._on_cinder_volume_relation_changed,
        )
        self.framework.observe(
            rel_observer.relation_departed,
            self._on_cinder_volume_relation_changed,
        )
        self.framework.observe(
            rel_observer.relation_broken,
            self._on_cinder_volume_relation_broken,
        )

    def _on_cinder_volume_relation_joined(self, event):
        """CinderVolume relation joined."""
        logging.debug("CinderVolumeRequires on_joined")
        self.on.connected.emit(event.relation)

    def _on_cinder_volume_relation_changed(self, event):
        """CinderVolume relation changed."""
        logging.debug("CinderVolumeRequires on_changed")
        if self.provider_ready():
            self.on.ready.emit(event.relation)

    def _on_cinder_volume_relation_broken(self, event):
        """CinderVolume relation broken."""
        logging.debug("CinderVolumeRequires on_broken")
        self.on.goneaway.emit(event.relation)

    def snap_name(self) -> str | None:
        """Return the snap name."""
        relation = self.model.get_relation(self.relation_name)
        if relation is None:
            return None
        sub_unit = remote_unit(relation)
        if sub_unit is None:
            logger.debug("No remote unit yet")
            return None
        return relation.data[sub_unit].get(SNAP_KEY)

    def provider_ready(self) -> bool:
        return self.snap_name() is not None

    def set_ready(self) -> None:
        """Communicate Cinder backend is ready."""
        logging.debug("Signaling backend has been configured")
        relation = self.model.get_relation(self.relation_name)
        if relation is not None:
            relation.data[self.model.unit][BACKEND_KEY] = self.backend_key


class DriverReadyEvent(ops.RelationChangedEvent):
    """Driver Ready Event."""


class DriverGoneEvent(ops.RelationBrokenEvent):
    """Driver Gone Event."""


class CinderVolumeClientEvents(ops.ObjectEvents):
    """Events class for `on`"""

    driver_ready = ops.EventSource(DriverReadyEvent)
    driver_gone = ops.EventSource(DriverGoneEvent)


class CinderVolumeProvides(ops.Object):
    """
    CinderVolumeProvides class
    """

    on = CinderVolumeClientEvents()  # type: ignore

    def __init__(
        self, charm: ops.CharmBase, relation_name: str, snap_name: str
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.snap_name = snap_name
        rel_observer = self.charm.on[relation_name]
        self.framework.observe(
            rel_observer.relation_joined,
            self._on_cinder_volume_relation_joined,
        )
        self.framework.observe(
            rel_observer.relation_changed,
            self._on_cinder_volume_relation_changed,
        )
        self.framework.observe(
            rel_observer.relation_broken,
            self._on_cinder_volume_relation_broken,
        )

    def _on_cinder_volume_relation_joined(
        self, event: ops.RelationJoinedEvent
    ):
        """Handle CinderVolume joined."""
        logging.debug("CinderVolumeProvides on_joined")
        self.publish_snap(event.relation)

    def _on_cinder_volume_relation_changed(
        self, event: ops.RelationChangedEvent
    ):
        """Handle CinderVolume changed."""
        logging.debug("CinderVolumeProvides on_changed")
        if self.requirer_ready(event.relation):
            self.on.driver_ready.emit(event.relation)

    def _on_cinder_volume_relation_broken(
        self, event: ops.RelationBrokenEvent
    ):
        """Handle CinderVolume broken."""
        logging.debug("CinderVolumeProvides on_departed")
        self.on.driver_gone.emit(event.relation)

    def requirer_backend(self, relation: ops.Relation) -> str | None:
        sub_unit = remote_unit(relation)
        if sub_unit is None:
            logger.debug("No remote unit yet")
            return None
        return relation.data[sub_unit].get(BACKEND_KEY)

    def requirer_ready(self, relation: ops.Relation) -> bool:
        return self.requirer_backend(relation) is not None

    def publish_snap(self, relation: ops.Relation):
        """Publish snap name to relation."""
        relation.data[self.model.unit][SNAP_KEY] = self.snap_name
