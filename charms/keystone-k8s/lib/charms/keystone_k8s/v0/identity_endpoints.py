"""IdentityEndpointsProvides and Requires module.

This library contains the Requires and Provides classes for handling
the identity_endpoints interface.
"""

import json
import logging

from ops import ModelError
from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)
from ops.model import (
    Relation,
    SecretNotFoundError,
)

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
# TODO: change this once someone with enough privileges calls
# "charmcraft create-lib identity_endpoints"
LIBID = "fab60b47-4b58-48d8-9589-383af9ebb3d0"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


logger = logging.getLogger(__name__)


class IdentityEndpointsConnectedEvent(EventBase):
    """IdentityEndpoints connected Event."""

    pass


class IdentityEndpointsChangedEvent(EventBase):
    """IdentityEndpoints ready for use Event."""

    pass


class IdentityEndpointsGoneAwayEvent(EventBase):
    """IdentityEndpoints relation has gone-away Event"""

    pass


class IdentityEndpointsServerEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(IdentityEndpointsConnectedEvent)
    changed = EventSource(IdentityEndpointsChangedEvent)
    goneaway = EventSource(IdentityEndpointsGoneAwayEvent)


class IdentityEndpointsRequires(Object):
    """
    IdentityEndpointsRequires class
    """

    on = IdentityEndpointsServerEvents()
    _stored = StoredState()

    def __init__(
        self,
        charm,
        relation_name: str,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_identity_endpoints_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_endpoints_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_endpoints_relation_broken,
        )

    def _on_identity_endpoints_relation_joined(self, event):
        """IdentityEndpoints relation joined."""
        logging.debug("IdentityEndpoints on_joined")
        self.on.connected.emit()

    def _on_identity_endpoints_relation_changed(self, event):
        """IdentityEndpoints relation changed."""
        logging.debug("IdentityEndpoints on_changed")
        try:
            self.on.changed.emit()
        except (AttributeError, KeyError, ModelError):
            pass

    def _on_identity_endpoints_relation_broken(self, event):
        """IdentityEndpoints relation broken."""
        logging.debug("IdentityEndpoints on_broken")
        self.on.goneaway.emit()

    @property
    def _identity_endpoints_rel(self) -> Relation:
        """The IdentityEndpoints relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> str:
        """Return the value for the given key from remote app data."""
        data = self._identity_endpoints_rel.data[self._identity_endpoints_rel.app]
        return data.get(key)

    @property
    def endpoints(self) -> list[dict]:
        """Return the Keystone endpoints."""
        try:
            endpoints_str = self.get_remote_app_data("endpoints") or ""
            return json.loads(endpoints_str) or []
        except (AttributeError, KeyError):
            return []


class HasIdentityEndpointsClientsEvent(EventBase):
    """HasIdentityEndpointsClients Event."""

    pass


class ReadyIdentityEndpointsClientsEvent(EventBase):
    """IdentityEndpointsClients Ready Event."""

    def __init__(
        self,
        handle,
        relation_id,
        relation_name,
        client_app_name,
    ):
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name
        self.client_app_name = client_app_name

    def snapshot(self):
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
            "client_app_name": self.client_app_name,
        }

    def restore(self, snapshot):
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]
        self.client_app_name = snapshot["client_app_name"]


class IdentityEndpointsClientEvents(ObjectEvents):
    """Events class for `on`"""

    has_identity_endpoints_clients = EventSource(HasIdentityEndpointsClientsEvent)
    ready_identity_endpoints_clients = EventSource(
        ReadyIdentityEndpointsClientsEvent
    )


class IdentityEndpointsProvides(Object):
    """
    IdentityEndpointsProvides class
    """

    on = IdentityEndpointsClientEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_identity_endpoints_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_endpoints_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_endpoints_relation_broken,
        )

    def _on_identity_endpoints_relation_joined(self, event):
        """Handle IdentityEndpoints joined."""
        logging.debug("IdentityEndpoints on_joined")
        self.on.has_identity_endpoints_clients.emit()

    def _on_identity_endpoints_relation_changed(self, event):
        """Handle IdentityEndpoints changed."""
        logging.debug("IdentityEndpoints on_changed")
        self.on.ready_identity_endpoints_clients.emit(
            event.relation.id,
            event.relation.name,
            event.relation.app.name)

    def _on_identity_endpoints_relation_broken(self, event):
        """Handle IdentityEndpoints broken."""
        logging.debug("IdentityEndpointsProvides on_broken")

    def set_identity_endpoints(
        self,
        relation_name: int,
        relation_id: str,
        endpoints: list[dict],
    ):
        logging.debug("Setting identity_endpoints connection information.")
        for relation in self.framework.model.relations[relation_name]:
            if relation.id == relation_id:
                app_data = relation.data[self.charm.app]
                app_data["endpoints"] = json.dumps(endpoints)

    def set_identity_endpoints_all_relations(
        self,
        relation_name: int,
        endpoints: list[dict],
    ):
        logging.debug("Updating all endpoint listener relations.")
        for relation in self.framework.model.relations[relation_name]:
            app_data = relation.data[self.charm.app]
            app_data["endpoints"] = json.dumps(endpoints)
