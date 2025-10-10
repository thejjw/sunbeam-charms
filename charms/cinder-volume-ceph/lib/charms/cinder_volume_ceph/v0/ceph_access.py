"""CephAccess Provides and Requires module.

This library contains the Requires and Provides classes for handling
the ceph-access interface.

Import `CephAccessRequires` in your charm, with the charm object and the
relation name:
    - self
    - "ceph_access"

Three events are also available to respond to:
    - connected
    - ready
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.cinder_volume_ceph.v0.ceph_access import CephAccessRequires

class CephAccessClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # CephAccess Requires
        self.ceph_access = CephAccessRequires(
            self,
            relation_name="ceph_access",
        )
        self.framework.observe(
            self.ceph_access.on.connected, self._on_ceph_access_connected)
        self.framework.observe(
            self.ceph_access.on.ready, self._on_ceph_access_ready)
        self.framework.observe(
            self.ceph_access.on.goneaway, self._on_ceph_access_goneaway)

    def _on_ceph_access_connected(self, event):
        '''React to the CephAccess connected event.

        This event happens when n CephAccess relation is added to the
        model before credentials etc have been provided.
        '''
        # Do something before the relation is complete
        pass

    def _on_ceph_access_ready(self, event):
        '''React to the CephAccess ready event.

        This event happens when an CephAccess relation is removed.
        '''
        # IdentityService Relation has goneaway. shutdown services or suchlike
        pass

```

"""

# The unique Charmhub library identifier, never change it
LIBID = "89bf7a44286348bca5eb70e096487bda"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

import logging
from typing import Optional
from ops import RelationEvent
from ops.model import (
    Relation,
    SecretNotFoundError,
)
from ops.framework import (
    EventBase,
    ObjectEvents,
    EventSource,
    Object,
)

logger = logging.getLogger(__name__)


class CephAccessConnectedEvent(EventBase):
    """CephAccess connected Event."""

    pass


class CephAccessReadyEvent(EventBase):
    """CephAccess ready for use Event."""

    pass


class CephAccessGoneAwayEvent(EventBase):
    """CephAccess relation has gone-away Event"""

    pass


class CephAccessServerEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(CephAccessConnectedEvent)
    ready = EventSource(CephAccessReadyEvent)
    goneaway = EventSource(CephAccessGoneAwayEvent)


class CephAccessRequires(Object):
    """
    CephAccessRequires class
    """

    on = CephAccessServerEvents()

    def __init__(self, charm, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_ceph_access_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ceph_access_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_ceph_access_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ceph_access_relation_broken,
        )
        self._credentials = None

    @property
    def _ceph_access_rel(self) -> Relation:
        """The CephAccess relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> Optional[str]:
        """Return the value for the given key from remote app data."""
        data = self._ceph_access_rel.data[self._ceph_access_rel.app]
        return data.get(key)

    def _on_ceph_access_relation_joined(self, event):
        """CephAccess relation joined."""
        logging.debug("CephAccess on_joined")
        self.on.connected.emit()

    def _on_ceph_access_relation_changed(self, event):
        """CephAccess relation changed."""
        logging.debug("CephAccess on_changed")
        try:
            if self.ready:
                self.on.ready.emit()
        except (AttributeError, KeyError):
            pass

    def _on_ceph_access_relation_broken(self, event):
        """CephAccess relation broken."""
        logging.debug("CephAccess on_broken")
        self.on.goneaway.emit()

    def _retrieve_credentials(self) -> dict | None:
        if credentials := self._credentials:
            return credentials
        credentials_id = self.get_remote_app_data("access-credentials")
        if not credentials_id:
            return None
        try:
            credentials = self.model.get_secret(id=credentials_id).get_content(
                refresh=True
            )
        except SecretNotFoundError:
            logger.warning(f"Secret {credentials_id} not found")
            return None
        self._credentials = credentials
        return credentials

    @property
    def ceph_access_data(self) -> dict:
        """Return the service_password."""
        return self._retrieve_credentials() or {}

    @property
    def ready(self) -> bool:
        """Return the service_password."""
        ceph_access_data = self.ceph_access_data
        return all(k in ceph_access_data for k in ["uuid", "key"])


class HasCephAccessClientsEvent(EventBase):
    """Has CephAccessClients Event."""

    pass


class ReadyCephAccessClientsEvent(RelationEvent):
    """Has ReadyCephAccessClients Event."""

    pass


class CephAccessClientEvents(ObjectEvents):
    """Events class for `on`"""

    has_ceph_access_clients = EventSource(HasCephAccessClientsEvent)
    ready_ceph_access_clients = EventSource(ReadyCephAccessClientsEvent)


class CephAccessProvides(Object):
    """
    CephAccessProvides class
    """

    on = CephAccessClientEvents()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_ceph_access_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ceph_access_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ceph_access_relation_broken,
        )

    def _on_ceph_access_relation_joined(self, event):
        """Handle CephAccess joined."""
        logging.debug("CephAccess on_joined")
        self.on.has_ceph_access_clients.emit()

    def _on_ceph_access_relation_changed(self, event):
        """Handle CephAccess joined."""
        logging.debug("CephAccess on_changed")
        self.on.ready_ceph_access_clients.emit(
            event.relation, app=event.app, unit=event.unit
        )

    def _on_ceph_access_relation_broken(self, event):
        """Handle CephAccess broken."""
        logging.debug("CephAccessProvides on_broken")

    def set_ceph_access_credentials(
        self, relation_name: int, relation_id: str, access_credentials: str
    ):

        logging.debug("Setting ceph_access connection information.")
        _ceph_access_rel = None
        for relation in self.framework.model.relations[relation_name]:
            if relation.id == relation_id:
                _ceph_access_rel = relation
        if not _ceph_access_rel:
            # Relation has disappeared so skip send of data
            return
        app_data = _ceph_access_rel.data[self.charm.app]
        app_data["access-credentials"] = access_credentials
