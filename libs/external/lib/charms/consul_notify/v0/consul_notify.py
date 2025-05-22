"""ConsulNotify Provides and Requires module.

This library contains Provider and Requirer classes for
consul-notify interface.

The provider side updates relation data with the snap name and unix socket path
information required by consul agents running in client mode
or consul users/clients.

The requirer side receives the snap name and unix socket file path via relation data.

## Requirer Example

Import `ConsulNotifyRequirer` in your charm, with the charm object and the
relation name:
    - self
    - "consul-notify"

An event is also available to respond to:
    - notify_parameters_changed

A basic example showing the usage of the requirer side:

```
from charms.consul_notify.v0.consul_notify import (
    ConsulNotifyRequirer
)

class ConsulClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulNotify Requires
        self.consul_notify = ConsulNotifyRequirer(
            self, "consul-notify",
        )
        self.framework.observe(
            self.consul_notify.on.notify_parameters_changed,
            self._on_notify_parameters_changed
        )

    def _on_notify_parameters_changed(self, event):
        '''React to the Consul notify parameters changed event.

        This event happens when consul-notify relation is added to the
        model and relation data is changed.
        '''
        # Do something with the parameters provided by relation.
        # For example:
        snap_name = self.consul_notify.snap_name
        socket_path = self.consul_notify.unix_socket_filepath
        if snap_name and socket_path:
            # Use the parameters for configuration
            pass
```

## Provider Example

Import `ConsulNotifyProvider` in your charm, with the charm object and the
relation name:
    - self
    - "consul-notify"

An event is also available to respond to:
    - notify_parameter_request

A basic example showing the usage of the provider side:

```python
from charms.consul_notify.v0.consul_notify import (
    ConsulNotifyProvider
)

class ServiceCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulNotify Provider
        self.consul_notify = ConsulNotifyProvider(
            self, "consul-notify",
        )
        self.framework.observe(
            self.consul_notify.on.notify_parameter_request,
            self._on_notify_parameter_request
        )

    def _on_notify_parameter_request(self, event):
        '''React to the notify parameter request event.

        This event happens when a client charm relates to this charm
        and requests notification parameters.
        '''
        # Provide the snap name and socket path to the requirer
        self.consul_notify.set_consul_notify_parameters(
            relation=event.relation,
            snap_name="my-consul-snap",
            unix_socket_filepath="/var/snap/my-consul-snap/common/consul.sock"
        )
```
"""

import logging

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationEvent,
)
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation
from pydantic import BaseModel, Field, ValidationError

# The unique Charmhub library identifier, never change it
LIBID = "a7b5d3c9e1f8d2a4b6c5d3e2f1a9b8c7"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

DEFAULT_RELATION_NAME = "consul-notify"

logger = logging.getLogger(__name__)


class ConsulNotifyAppData(BaseModel):
    """Notification parameters from the provider."""

    snap_name: str = Field("The name of the snap")
    unix_socket_filepath: str = Field("The UNIX socket file name")


class NotifyParametersChangedEvent(RelationEvent):
    """Notification parameters changed event."""

    pass


class ConsulNotifyRequirerEvents(ObjectEvents):
    """Consul Notify requirer events."""

    notify_parameters_changed = EventSource(NotifyParametersChangedEvent)


class ConsulNotifyRequirer(Object):
    """Class to be instantiated on the requirer side of the relation."""

    on = ConsulNotifyRequirerEvents()  # pyright: ignore

    def __init__(
        self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(
            events.relation_changed, self._on_relation_changed
        )
        self.framework.observe(
            events.relation_broken, self._on_relation_broken
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle relation changed event."""
        if self._validate_databag_from_relation():
            self.on.notify_parameters_changed.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle relation broken event."""
        self.on.notify_parameters_changed.emit(event.relation)

    def _validate_databag_from_relation(self) -> bool:
        try:
            if self._consul_notify_rel:
                databag = self._consul_notify_rel.data[
                    self._consul_notify_rel.app
                ]
                ConsulNotifyAppData(**databag)  # type: ignore
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")
            return False

        return True

    def _get_app_databag_from_relation(self) -> dict:
        try:
            if self._consul_notify_rel:
                databag = self._consul_notify_rel.data[
                    self._consul_notify_rel.app
                ]
                data = ConsulNotifyAppData(**databag)  # type: ignore
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
        """Return snap_name from provider app data.

        Returns:
            The name of the snap that provides the consul socket, or None if not available
        """
        data = self._get_app_databag_from_relation()
        return data.get("snap_name")

    @property
    def unix_socket_filepath(self) -> str | None:
        """Return UNIX socket filepath from provider app data.

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


class NotifyParametersRequestEvent(RelationEvent):
    """Consul notify parameters request event."""

    pass


class ConsulNotifyProviderEvents(ObjectEvents):
    """Events class for `on`."""

    notify_parameter_request = EventSource(NotifyParametersRequestEvent)


class ConsulNotifyProvider(Object):
    """Class to be instantiated on the provider side of the relation."""

    on = ConsulNotifyProviderEvents()  # pyright: ignore

    def __init__(
        self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(
            events.relation_changed, self._on_relation_changed
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle new client connection requesting notification parameters."""
        self.on.notify_parameter_request.emit(event.relation)

    def set_consul_notify_parameters(
        self,
        relation: Relation | None,
        snap_name: str,
        unix_socket_filepath: str,
    ) -> None:
        """Set consul notification parameters on the relation.

        Args:
            relation: The specific relation to update, or None to update all relations
            snap_name: The name of the snap that provides the consul socket
            unix_socket_filepath: The path to the UNIX socket file
        """
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set parameters")
            return

        try:
            databag = ConsulNotifyAppData(
                snap_name=snap_name,
                unix_socket_filepath=unix_socket_filepath,
            )
        except ValidationError as e:
            logger.info(f"Provider trying to set incorrect app data {str(e)}")
            return

        _snap_name: str = databag.snap_name
        _unix_socket_filepath: str = databag.unix_socket_filepath

        # If relation is None, send parameters to all related applications
        if relation is None:
            logging.debug(
                f"Sending parameters to all related applications of relation {self.relation_name}"
            )
            relations_to_update = self.framework.model.relations[
                self.relation_name
            ]
        else:
            logging.debug(
                f"Sending parameters on relation {relation.app.name} {relation.name}/{relation.id}"
            )
            relations_to_update = [relation]

        for rel in relations_to_update:
            if rel and rel.app:
                rel.data[self.charm.app]["snap_name"] = _snap_name
                rel.data[self.charm.app][
                    "unix_socket_filepath"
                ] = _unix_socket_filepath
