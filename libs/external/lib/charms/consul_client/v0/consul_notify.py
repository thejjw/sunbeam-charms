"""ConsulNotify Provides and Requires module.

This library contains Provider and Requirer classes for
consul-notify interface.

The provider side offers the service of network monitoring and notification.
It monitors network connectivity to servers and notifies when issues are detected.

The requirer side receives notifications about network failures
and provides the socket path where it wants to receive these notifications.

## Provider Example

Import `ConsulNotifyProvider` in your charm, with the charm object and the
relation name:
    - self
    - "consul-notify"

An event is also available to respond to:
    - socket_available

A basic example showing the usage of the provider side:

```python
from charms.consul_client.v0.consul_notify import (
    ConsulNotifyProvider
)

class MyProviderCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulNotify Provider
        self.consul_notify = ConsulNotifyProvider(
            self, "consul-notify",
        )
        self.framework.observe(
            self.consul_notify.on.socket_available,
            self._on_socket_available
        )

    def _on_socket_available(self, event):
        '''React to the socket available event.

        This event happens when a requirer charm relates to this charm
        and provides its socket information.
        '''
        # Get the socket information
        snap_name = self.consul_notify.snap_name
        socket_path = self.consul_notify.unix_socket_filepath

        if snap_name and socket_path:
            # Configure TCP health check with the socket information
            # This will enable monitoring and notification
            self._configure_tcp_health_check(snap_name, socket_path)
```

## Requirer Example

Import `ConsulNotifyRequirer` in your charm, with the charm object and the
relation name:
    - self
    - "consul-notify"

An event is also available to respond to:
    - relation_ready

A basic example showing the usage of the requirer side:

```python
from charms.consul_client.v0.consul_notify import (
    ConsulNotifyRequirer
)

class MyRequirerCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulNotify Requires
        self.consul_notify = ConsulNotifyRequirer(
            self, "consul-notify",
        )

        # Observe the relation_ready event
        self.framework.observe(
            self.consul_notify.on.relation_ready,
            self._on_consul_notify_ready
        )

    def _on_consul_notify_ready(self, event):
        '''React to the consul-notify relation being ready.

        This event happens when the relation is created or joined.
        '''
        # Set the socket information for the provider
        self.consul_notify.set_socket_info(
            snap_name="my-service-snap",
            unix_socket_filepath="/var/snap/my-service-snap/common/socket.sock"
        )
```
"""

import logging

from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent, RelationEvent
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation
from pydantic import BaseModel, Field, ValidationError

# The unique Charmhub library identifier, never change it
LIBID = "1edb80abcec14c1e86a55f7e3809030a"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2

DEFAULT_RELATION_NAME = "consul-notify"

logger = logging.getLogger(__name__)


class SocketInfoData(BaseModel):
    """Socket information from the requirer."""

    snap_name: str = Field("The name of the snap that provides the socket")
    unix_socket_filepath: str = Field("The UNIX socket file path")


class SocketAvailableEvent(RelationEvent):
    """Socket information available event."""

    pass


class SocketGoneEvent(RelationEvent):
    """Socket information gone event."""

    pass


class ConsulNotifyProviderEvents(ObjectEvents):
    """Consul Notify provider events."""

    socket_available = EventSource(SocketAvailableEvent)
    socket_gone = EventSource(SocketGoneEvent)


class ConsulNotifyProvider(Object):
    """Class to be instantiated on the provider side of the relation."""

    on = ConsulNotifyProviderEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)
        self.framework.observe(events.relation_broken, self._on_relation_broken)

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle relation changed event."""
        if self._validate_databag_from_relation():
            self.on.socket_available.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle relation broken event."""
        self.on.socket_gone.emit(event.relation)

    def _validate_databag_from_relation(self) -> bool:
        try:
            if self._consul_notify_rel:
                databag = self._consul_notify_rel.data[self._consul_notify_rel.app]
                SocketInfoData(**databag)  # type: ignore
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")
            return False

        return True

    def _get_app_databag_from_relation(self) -> dict:
        try:
            if self._consul_notify_rel:
                databag = self._consul_notify_rel.data[self._consul_notify_rel.app]
                data = SocketInfoData(**databag)  # type: ignore
                return data.model_dump()
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")

        return {}

    @property
    def _consul_notify_rel(self) -> Relation | None:
        """The Consul Notify relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def snap_name(self) -> str | None:
        """Return snap_name from requirer app data.

        Returns:
            The name of the snap that provides the socket, or None if not available
        """
        data = self._get_app_databag_from_relation()
        return data.get("snap_name")

    @property
    def unix_socket_filepath(self) -> str | None:
        """Return UNIX socket filepath from requirer app data.

        Returns:
            The path to the UNIX socket file, or None if not available
        """
        data = self._get_app_databag_from_relation()
        return data.get("unix_socket_filepath")

    @property
    def is_ready(self) -> bool:
        """Check if the relation is ready with all required data.

        Returns:
            True if both snap_name and unix_socket_filepath are available, False otherwise
        """
        return bool(self.snap_name and self.unix_socket_filepath)


class RelationReadyEvent(RelationEvent):
    """Relation ready event for the requirer side."""
    pass


class ConsulNotifyRequirerEvents(ObjectEvents):
    """Consul Notify requirer events."""

    relation_ready = EventSource(RelationReadyEvent)


class ConsulNotifyRequirer(Object):
    """Class to be instantiated on the requirer side of the relation."""

    on = ConsulNotifyRequirerEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_created, self._on_relation_created)
        self.framework.observe(events.relation_joined, self._on_relation_joined)

    def _on_relation_created(self, event: RelationEvent):
        """Handle relation created event."""
        self.on.relation_ready.emit(event.relation)

    def _on_relation_joined(self, event: RelationEvent):
        """Handle relation joined event."""
        self.on.relation_ready.emit(event.relation)

    def set_socket_info(
        self,
        snap_name: str,
        unix_socket_filepath: str,
    ) -> None:
        """Set socket information on the relation.

        Args:
            snap_name: The name of the snap that provides the socket
            unix_socket_filepath: The path to the UNIX socket file
        """
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set socket info")
            return

        try:
            databag = SocketInfoData(
                snap_name=snap_name,
                unix_socket_filepath=unix_socket_filepath,
            )
        except ValidationError as e:
            logger.info(f"Requirer trying to set incorrect app data {str(e)}")
            return

        _snap_name: str = databag.snap_name
        _unix_socket_filepath: str = databag.unix_socket_filepath

        for relation in self.framework.model.relations.get(self.relation_name, []):
            if relation and relation.app:
                logging.debug(
                    f"Setting socket info on relation {relation.app.name} {relation.name}/{relation.id}"
                )
                relation.data[self.charm.app]["snap_name"] = _snap_name
                relation.data[self.charm.app]["unix_socket_filepath"] = _unix_socket_filepath
