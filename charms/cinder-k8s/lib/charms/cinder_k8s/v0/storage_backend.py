"""TODO: Add a proper docstring here.

This is a placeholder docstring for this charm library. Docstrings are
presented on Charmhub and updated whenever you push a new version of the
library.

Complete documentation about creating and documenting libraries can be found
in the SDK docs at https://juju.is/docs/sdk/libraries.

See `charmcraft publish-lib` and `charmcraft fetch-lib` for details of how to
share and consume charm libraries. They serve to enhance collaboration
between charmers. Use a charmer's libraries for classes that handle
integration with their charm.

Bear in mind that new revisions of the different major API versions (v0, v1,
v2 etc) are maintained independently.  You can continue to update v0 and v1
after you have pushed v3.

Markdown is supported, following the CommonMark specification.
"""

# The unique Charmhub library identifier, never change it
LIBID = "68536ea2f06d40078ccbedd7095e141c"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

import json
import logging
import requests

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object,
)

from ops.model import Relation

from typing import List

logger = logging.getLogger(__name__)


# TODO: add your code here! Happy coding!
class StorageBackendConnectedEvent(EventBase):
    """StorageBackend connected Event."""

    pass


class StorageBackendReadyEvent(EventBase):
    """StorageBackend ready for use Event."""

    pass


class StorageBackendGoneAwayEvent(EventBase):
    """StorageBackend relation has gone-away Event"""

    pass


class StorageBackendServerEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(StorageBackendConnectedEvent)
    ready = EventSource(StorageBackendReadyEvent)
    goneaway = EventSource(StorageBackendGoneAwayEvent)


class StorageBackendRequires(Object):
    """
    StorageBackendRequires class
    """

    on = StorageBackendServerEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_storage_backend_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_storage_backend_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_storage_backend_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_storage_backend_relation_broken,
        )

    def _on_storage_backend_relation_joined(self, event):
        """StorageBackend relation joined."""
        logging.debug("StorageBackendRequires on_joined")
        self.on.connected.emit()

    def _on_storage_backend_relation_changed(self, event):
        """StorageBackend relation changed."""
        logging.debug("StorageBackendRequires on_changed")
        self.on.ready.emit()

    def _on_storage_backend_relation_broken(self, event):
        """StorageBackend relation broken."""
        logging.debug("StorageBackendRequires on_broken")
        self.on.goneaway.emit()

    def set_ready(self) -> None:
        """Request access to the StorageBackend server."""
        if self.model.unit.is_leader():
            logging.debug(
                "Signalling storage backends that core services are ready"
            )
            for relation in self.framework.model.relations[self.relation_name]:
                relation.data[self.charm.app]["ready"] = 'true'


class APIReadyEvent(EventBase):
    """StorageBackendClients Ready Event."""

    pass


class StorageBackendClientEvents(ObjectEvents):
    """Events class for `on`"""

    api_ready = EventSource(APIReadyEvent)


class StorageBackendProvides(Object):
    """
    StorageBackendProvides class
    """

    on = StorageBackendClientEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_storage_backend_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_storage_backend_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_storage_backend_relation_broken,
        )

    def _on_storage_backend_relation_joined(self, event):
        """Handle StorageBackend joined."""
        logging.debug("StorageBackendProvides on_joined")

    def remote_ready(self):
        relation = self.framework.model.get_relation(self.relation_name)
        if relation:
            ready = relation.data[relation.app].get("ready")
            return ready and json.loads(ready)
        return False

    def _on_storage_backend_relation_changed(self, event):
        """Handle StorageBackend changed."""
        logging.debug("StorageBackendProvides on_changed")
        if self.remote_ready():
            self.on.api_ready.emit()

    def _on_storage_backend_relation_broken(self, event):
        """Handle StorageBackend broken."""
        logging.debug("RabbitMQStorageBackendProvides on_departed")
        # TODO clear data on the relation
