"""CephNfsProvides and CephNfsRequires module.

This library contains the Provides and Requires classes for handling the
ceph-nfs-client interface.

Import `CephNfsRequires` in your charm, with the charm object and the relation
name:
    - self
    - "ceph-nfs"

Two events are also available to respond to:
    - connected
    - departed

A basic example showing the usage of this relation follows:

```
import charms.ceph_nfs.v0.ceph_nfs as ceph_nfs


class CephNfsClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # CephNfsRequires
        self._ceph_nfs = ceph_nfs.CephNfsRequires(
            self, "ceph-nfs",
        )
        self.framework.observe(
            self._ceph_nfs.on.connected,
            self._on_ceph_nfs_connected,
        )
        self.framework.observe(
            self._ceph_nfs.on.departed,
            self._on_ceph_nfs_departed,
        )

    def _on_ceph_nfs_connected(self, event):
        '''React to the CephNfsConnectedEvent event.

        This event happens when the ceph-nfs relation is added to the
        model before information has been provided.
        '''
        # Do something before the relation is complete.
        pass

    def _on_ceph_nfs_departed(self, event):
        '''React to the CephNfsDepartedEvent event.

        This event happens when ceph-nfs relation is removed.
        '''
        # ceph-nfs relation has departed. Shutdown services if needed.
        pass
```
"""

import logging

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationDepartedEvent,
    RelationEvent,
)
from ops.framework import (
    EventSource,
    Object,
    ObjectEvents,
)

# The unique Charmhub library identifier, never change it
LIBID = "0fb2d24550d94d24868d3cefa784d5be"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

logger = logging.getLogger(__name__)


class CephNfsConnectedEvent(RelationEvent):
    """ceph-nfs connected event."""

    pass


class CephNfsDepartedEvent(RelationEvent):
    """ceph-nfs relation departed event."""

    pass


class CephNfsReconcileEvent(RelationEvent):
    """ceph-nfs relation reconciliation event."""

    pass


class CephNfsProvidesEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_nfs_connected = EventSource(CephNfsConnectedEvent)
    ceph_nfs_departed = EventSource(CephNfsDepartedEvent)
    ceph_nfs_reconcile = EventSource(CephNfsReconcileEvent)


class CephNfsRequiresEvents(ObjectEvents):
    """Events class for `on`."""

    ceph_nfs_connected = EventSource(CephNfsConnectedEvent)
    ceph_nfs_departed = EventSource(CephNfsDepartedEvent)


class CephNfsProvides(Object):
    """Interface for ceph-nfs-client provider."""

    on = CephNfsProvidesEvents()

    def __init__(self, charm, relation_name="ceph-nfs"):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        # React to ceph-nfs relations.
        self.framework.observe(
            charm.on[relation_name].relation_joined, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[relation_name].relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            charm.on[relation_name].relation_departed, self._on_relation_departed
        )

        # React to ceph peers relations.
        self.framework.observe(charm.on["peers"].relation_departed, self._on_ceph_peers)
        self.framework.observe(charm.on["peers"].relation_changed, self._on_ceph_peers)

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Prepare relation for data from requiring side."""
        if not self.model.unit.is_leader():
            return

        logger.info("_on_relation_changed event")

        if not self.charm.ready_for_service():
            logger.info("Not processing request as service is not yet ready")
            event.defer()
            return

        self.on.ceph_nfs_connected.emit(event.relation)

    def _on_relation_departed(self, event: RelationDepartedEvent):
        """Cleanup relation after departure."""
        if not self.model.unit.is_leader() or event.relation.app == self.charm.app:
            return

        logger.info("_on_relation_departed event")
        self.on.ceph_nfs_departed.emit(event.relation)

    def _on_ceph_peers(self, event):
        """Handle ceph peers relation events."""
        if not self.model.unit.is_leader():
            return

        logger.info("_on_ceph_peers event")

        # Mon addrs might have changed, update the relation data if needed.
        # Additionally, new nodes may have been added, which could be added to
        # NFS clusters.
        for relation in self.framework.model.relations[self.relation_name]:
            self.on.ceph_nfs_reconcile.emit(relation)


class CephNfsRequires(Object):
    """CephNfsRequires class."""

    on = CephNfsRequiresEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)

        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ceph_nfs_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_ceph_nfs_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ceph_nfs_relation_broken,
        )

    def _on_ceph_nfs_relation_changed(self, event: RelationChangedEvent):
        """Handle ceph-nfs relation changed."""
        logging.debug("ceph-nfs relation changed")
        self.on.ceph_nfs_connected.emit(event.relation)

    def _on_ceph_nfs_relation_broken(self, event: RelationBrokenEvent):
        """Handle ceph-nfs relation broken."""
        logging.debug("ceph-nfs relation broken")
        self.on.ceph_nfs_departed.emit(event.relation)
