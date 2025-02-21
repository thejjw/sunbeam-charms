"""RabbitMQProvides and Requires module.

This library contains the Requires and Provides classes for handling
the rabbitmq interface.

Import `RabbitMQRequires` in your charm, with the charm object and the
relation name:
    - self
    - "amqp"

Also provide two additional parameters to the charm object:
    - username
    - vhost
    - external_connectivity: Optional, default False

Two events are also available to respond to:
    - connected
    - ready
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.rabbitmq_k8s.v0.rabbitmq import RabbitMQRequires

class RabbitMQClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # RabbitMQ Requires
        self.amqp = RabbitMQRequires(
            self, "amqp",
            username="myusername",
            vhost="vhostname"
        )
        self.framework.observe(
            self.amqp.on.connected, self._on_amqp_connected)
        self.framework.observe(
            self.amqp.on.ready, self._on_amqp_ready)
        self.framework.observe(
            self.amqp.on.goneaway, self._on_amqp_goneaway)

    def _on_amqp_connected(self, event):
        '''React to the RabbitMQ connected event.

        This event happens when n RabbitMQ relation is added to the
        model before credentials etc have been provided.
        '''
        # Do something before the relation is complete
        pass

    def _on_amqp_ready(self, event):
        '''React to the RabbitMQ ready event.

        The RabbitMQ interface will use the provided username and vhost for the
        request to the rabbitmq server.
        '''
        # RabbitMQ Relation is ready. Do something with the completed relation.
        pass

    def _on_amqp_goneaway(self, event):
        '''React to the RabbitMQ goneaway event.

        This event happens when an RabbitMQ relation is removed.
        '''
        # RabbitMQ Relation has goneaway. shutdown services or suchlike
        pass
```
"""

import json
import logging
import typing

import ops

# The unique Charmhub library identifier, never change it
LIBID = "45622352791142fd9cf87232e3bd6f2a"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3


logger = logging.getLogger(__name__)


class RabbitMQConnectedEvent(ops.EventBase):
    """RabbitMQ connected Event."""

    pass


class RabbitMQReadyEvent(ops.EventBase):
    """RabbitMQ ready for use Event."""

    pass


class RabbitMQGoneAwayEvent(ops.EventBase):
    """RabbitMQ relation has gone-away Event."""

    pass


class RabbitMQServerEvents(ops.ObjectEvents):
    """Events class for `on`."""

    connected = ops.EventSource(RabbitMQConnectedEvent)
    ready = ops.EventSource(RabbitMQReadyEvent)
    goneaway = ops.EventSource(RabbitMQGoneAwayEvent)


class RabbitMQRequires(ops.Object):
    """RabbitMQRequires class."""

    on = RabbitMQServerEvents()  # type: ignore

    def __init__(
        self,
        charm,
        relation_name: str,
        username: str,
        vhost: str,
        external_connectivity: bool = False,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.username = username
        self.vhost = vhost
        self.external_connectivity = external_connectivity
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_amqp_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_amqp_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_amqp_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_amqp_relation_broken,
        )

    def _on_amqp_relation_joined(self, event: ops.RelationJoinedEvent):
        logging.debug("RabbitMQRabbitMQRequires on_joined")
        self.on.connected.emit()
        self.request_access(
            self.username, self.vhost, self.external_connectivity
        )

    def _on_amqp_relation_changed(
        self, event: ops.RelationChangedEvent | ops.RelationDepartedEvent
    ):
        logging.debug("RabbitMQRabbitMQRequires on_changed/departed")
        if self.password:
            self.on.ready.emit()

    def _on_amqp_relation_broken(self, event: ops.RelationBrokenEvent):
        logging.debug("RabbitMQRabbitMQRequires on_broken")
        self.on.goneaway.emit()

    @property
    def _amqp_rel(self) -> ops.Relation | None:
        """The RabbitMQ relation."""
        return self.framework.model.get_relation(self.relation_name)

    def _get(self, key: str) -> str | None:
        """Return property from the RabbitMQ relation."""
        rel = self._amqp_rel
        if rel and rel.active:
            return rel.data[rel.app].get(key)
        return None

    @property
    def password(self) -> str | None:
        """Return the RabbitMQ password from the server side of the relation."""
        return self._get("password")

    @property
    def hostname(self) -> str | None:
        """Return the hostname from the RabbitMQ relation."""
        return self._get("hostname")

    @property
    def ssl_port(self) -> str | None:
        """Return the SSL port from the RabbitMQ relation."""
        return self._get("ssl_port")

    @property
    def ssl_ca(self) -> str | None:
        """Return the SSL port from the RabbitMQ relation."""
        return self._get("ssl_ca")

    @property
    def hostnames(self) -> list[str]:
        """Return a list of remote RMQ hosts from the RabbitMQ relation."""
        _hosts: list[str] = []
        rel = self._amqp_rel
        if not rel:
            return _hosts
        for unit in rel.units:
            if ingress := rel.data[unit].get("ingress-address"):
                _hosts.append(ingress)
        return _hosts

    def request_access(
        self, username: str, vhost: str, external_connectivity: bool
    ) -> None:
        """Request access to the RabbitMQ server."""
        if (rel := self._amqp_rel) and self.model.unit.is_leader():
            logging.debug("Requesting RabbitMQ user and vhost")
            rel.data[self.charm.app]["username"] = username
            rel.data[self.charm.app]["vhost"] = vhost
            rel.data[self.charm.app]["external_connectivity"] = json.dumps(
                external_connectivity
            )


class HasRabbitMQClientsEvent(ops.RelationEvent):
    """Has RabbitMQClients Event."""

    pass


class ReadyRabbitMQClientsEvent(ops.RelationEvent):
    """RabbitMQClients Ready Event."""

    pass


class GoneAwayRabbitMQClientsEvent(ops.RelationEvent):
    """RabbitMQClients GoneAway Event."""

    pass


class RabbitMQClientEvents(ops.ObjectEvents):
    """Events class for `on`."""

    has_amqp_clients = ops.EventSource(HasRabbitMQClientsEvent)
    ready_amqp_clients = ops.EventSource(ReadyRabbitMQClientsEvent)
    gone_away_amqp_clients = ops.EventSource(GoneAwayRabbitMQClientsEvent)


class RabbitMQProvides(ops.Object):
    """RabbitMQProvides class."""

    on = RabbitMQClientEvents()  # type: ignore

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        callback: typing.Callable,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.callback = callback
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_amqp_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_amqp_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_amqp_relation_broken,
        )

    def _on_amqp_relation_joined(self, event: ops.RelationJoinedEvent):
        """Handle RabbitMQ joined."""
        logging.debug(
            "RabbitMQRabbitMQProvides on_joined data={}".format(
                event.relation.data[event.relation.app]
            )
        )
        self.on.has_amqp_clients.emit(event.relation)

    def _on_amqp_relation_changed(self, event: ops.RelationChangedEvent):
        """Handle RabbitMQ changed."""
        relation = event.relation
        logging.debug(
            "RabbitMQRabbitMQProvides on_changed data={}".format(
                relation.data[relation.app]
            )
        )
        # Validate data on the relation
        if self.username(relation) and self.vhost(relation):
            self.on.ready_amqp_clients.emit(relation)
            if self.charm.unit.is_leader():
                self.callback(
                    event,
                    self.username(relation),
                    self.vhost(relation),
                    self.external_connectivity(relation),
                )
        else:
            logging.warning(
                "Received RabbitMQ changed event without the "
                "expected keys ('username', 'vhost') in the "
                "application data bag.  Incompatible charm in "
                "other end of relation?"
            )

    def _on_amqp_relation_broken(self, event: ops.RelationBrokenEvent):
        """Handle RabbitMQ broken."""
        logging.debug("RabbitMQRabbitMQProvides on_departed")
        self.on.gone_away_amqp_clients.emit(event.relation)

    def _get(self, relation: ops.Relation, key: str) -> str | None:
        """Return property from the RabbitMQ relation."""
        return relation.data[relation.app].get(key)

    def username(self, relation: ops.Relation) -> str | None:
        """Return the RabbitMQ username from the client side of the relation."""
        return self._get(relation, "username")

    def vhost(self, relation: ops.Relation) -> str | None:
        """Return the RabbitMQ vhost from the client side of the relation."""
        return self._get(relation, "vhost")

    def external_connectivity(self, relation: ops.Relation) -> bool:
        """Return the RabbitMQ external_connectivity from the client side of the relation."""
        return json.loads(
            self._get(relation, "external_connectivity") or "false"
        )
