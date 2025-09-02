"""Manila Provides and Requires module.

This library contains the Requires and Provides classes for handling
the manila interface.

Import `ManilaRequires` in your charm, with the charm object and the
relation name:
    - self
    - "manila"

Two events are also available to respond to:
    - connected
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.manila_k8s.v0.manila as manila_k8s


class ManilaClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # Manila Requires
        self._manila = manila_k8s.ManilaRequires(
            self, "manila",
        )
        self.framework.observe(
            self._manila.on.connected,
            self._on_manila_connected,
        )
        self.framework.observe(
            self._manila.on.goneaway,
            self._on_manila_goneaway,
        )

    def _on_manila_connected(self, event):
        '''React to the ManilaConnectedEvent event.

        This event happens when the manila relation is added to the
        model before information has been provided.
        '''
        # Do something before the relation is complete.
        pass

    def _on_manila_goneaway(self, event):
        '''React to the ManilaGoneAwayEvent event.

        This event happens when manila relation is removed.
        '''
        # manila relation has goneaway. Shutdown services if needed.
        pass
```
"""

import logging
from typing import List

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationJoinedEvent,
    RelationEvent,
)
from ops.framework import (
    EventSource,
    Object,
    ObjectEvents,
)
from ops.model import (
    Relation,
)

# The unique Charmhub library identifier, never change it
LIBID = "c074a92802f74a6f8460ae1875707a02"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


SHARE_PROTOCOL = "share_protocol"


class ManilaConnectedEvent(RelationEvent):
    """manila connected event."""

    pass


class ManilaGoneAwayEvent(RelationEvent):
    """manila relation has gone-away event"""

    pass


class ManilaEvents(ObjectEvents):
    """Events class for `on`."""

    manila_connected = EventSource(ManilaConnectedEvent)
    manila_goneaway = EventSource(ManilaGoneAwayEvent)


class ManilaProvides(Object):
    """ManilaProvides class."""

    on = ManilaEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_manila_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_manila_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_manila_relation_broken,
        )

    def _on_manila_relation_joined(self, event: RelationJoinedEvent):
        """Handle manila relation joined."""
        logging.debug("manila relation joined")
        self.on.manila_connected.emit(event.relation)

    def _on_manila_relation_broken(self, event: RelationBrokenEvent):
        """Handle manila relation broken."""
        logging.debug("manila relation broken")
        self.on.manila_goneaway.emit(event.relation)

    @property
    def _manila_rel(self) -> Relation | None:
        """The manila relation."""
        return self.framework.model.get_relation(self.relation_name)

    def update_share_protocol(self, share_protocol: str | None):
        """Updates the share protocol in the manila relation."""

        data = self._manila_rel.data[self.model.app]
        if share_protocol:
            data[SHARE_PROTOCOL] = share_protocol
        else:
            data.pop(SHARE_PROTOCOL, None)


class ManilaRequires(Object):
    """ManilaRequires class."""

    on = ManilaEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_manila_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_manila_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_manila_relation_broken,
        )

    def _on_manila_relation_changed(self, event: RelationChangedEvent):
        """Handle manila relation changed."""
        logging.debug("manila relation changed")
        self.on.manila_connected.emit(event.relation)

    def _on_manila_relation_broken(self, event: RelationBrokenEvent):
        """Handle manila relation broken."""
        logging.debug("manila relation broken")
        self.on.manila_goneaway.emit(event.relation)

    @property
    def share_protocols(self) -> List[str]:
        """Get the manila share protocols from the manila relations."""
        protocols = set()
        for relation in self.model.relations[self.relation_name]:
            app_data = relation.data[relation.app]
            if app_data.get(SHARE_PROTOCOL):
                protocols.add(app_data[SHARE_PROTOCOL])

        return list(protocols)
