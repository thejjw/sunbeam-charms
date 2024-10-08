"""ConsulCluster Provides and Requires module.

This library contains Provider and Requirer classes for
consul-cluster interface.

The provider side updates relation data with the endpoints
information required by consul agents running in client mode
or consul users/clients.

The requirer side receives the endpoints via relation data.
Example on how to use Requirer side using this library.

Import `ConsulEndpointsRequirer` in your charm, with the charm object and the
relation name:
    - self
    - "consul-cluster"

Two events are also available to respond to:
    - endpoints_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.consul_k8s.v0.consul_cluster import (
    ConsulEndpointsRequirer
)

class ConsulClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # ConsulCluster Requires
        self.consul = ConsulEdnpointsRequirer(
            self, "consul-cluster",
        )
        self.framework.observe(
            self.consul.on.endpoints_changed,
            self._on_consul_service_endpoints_changed
        )
        self.framework.observe(
            self.consul.on.goneaway,
            self._on_consul_service_goneaway
        )

    def _on_consul_service_endpoints_changed(self, event):
        '''React to the Consul service endpoints changed event.

        This event happens when consul-cluster relation is added to the
        model and relation data is changed.
        '''
        # Do something with the endpoints provided by relation.
        pass

    def _on_consul_service_goneaway(self, event):
        '''React to the ConsulService goneaway event.

        This event happens when consul-cluster relation is removed.
        '''
        # ConsulService Relation has goneaway.
        pass
```
"""

import json
import logging

from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent, RelationEvent
from ops.framework import EventSource, Object, ObjectEvents
from ops.model import Relation
from pydantic import BaseModel, Field, ValidationError, field_validator

# The unique Charmhub library identifier, never change it
LIBID = "f10432d106524b82ba68aa6eddbc3308"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

DEFAULT_RELATION_NAME = "consul-cluster"

logger = logging.getLogger(__name__)


class ConsulServiceProviderAppData(BaseModel):
    """Cluster endpoints from Consul server."""

    datacenter: str = Field("Datacenter cluster name")

    # All endpoints are json serialized
    internal_gossip_endpoints: list[str] | None = Field(
        "Consul server join addresses for internal consul agents"
    )
    external_gossip_endpoints: list[str] | None = Field(
        "Consul server join addresses for external consul agents"
    )
    internal_http_endpoint: str | None = Field(
        "Consul server http address for consul users running in same k8s cluster as consul-server"
    )
    # This field will be the ingress endpoint. Ingress is not supported yet.
    external_http_endpoint: str | None = Field("Consul server http address for external users")

    @field_validator("internal_gossip_endpoints", "external_gossip_endpoints", mode="before")
    @classmethod
    def convert_str_to_list_of_str(cls, v: str) -> list[str]:
        """Convert string field to list of str."""
        if not isinstance(v, str):
            return v

        try:
            return json.loads(v)
        except json.decoder.JSONDecodeError:
            raise ValueError("Field not in json format")

    @field_validator("internal_http_endpoint", "external_http_endpoint", mode="before")
    @classmethod
    def convert_str_null_to_none(cls, v: str) -> str | None:
        """Convert null string to None."""
        if v == "null":
            return None

        return v


class ClusterEndpointsChangedEvent(RelationEvent):
    """Consul cluster endpoints changed event."""

    pass


class ClusterServerGoneAwayEvent(RelationEvent):
    """Cluster server relation gone away event."""

    pass


class ConsulEndpointsRequirerEvents(ObjectEvents):
    """Consul Cluster requirer events."""

    endpoints_changed = EventSource(ClusterEndpointsChangedEvent)
    goneaway = EventSource(ClusterServerGoneAwayEvent)


class ConsulEndpointsRequirer(Object):
    """Class to be instantiated on the requirer side of the relation."""

    on = ConsulEndpointsRequirerEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)
        self.framework.observe(events.relation_broken, self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent):
        if self._validate_databag_from_relation():
            self.on.endpoints_changed.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle relation broken event."""
        self.on.goneaway.emit()

    def _validate_databag_from_relation(self) -> bool:
        try:
            if self._consul_cluster_rel:
                databag = self._consul_cluster_rel.data[self._consul_cluster_rel.app]
                ConsulServiceProviderAppData(**databag)  # type: ignore
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")
            return False

        return True

    def _get_app_databag_from_relation(self) -> dict:
        try:
            if self._consul_cluster_rel:
                databag = self._consul_cluster_rel.data[self._consul_cluster_rel.app]
                data = ConsulServiceProviderAppData(**databag)  # type: ignore
                return data.model_dump()
        except ValidationError as e:
            logger.info(f"Incorrect app databag: {str(e)}")

        return {}

    @property
    def _consul_cluster_rel(self) -> Relation | None:
        """The Consul cluster relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def datacenter(self) -> str | None:
        """Return datacenter name from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("datacenter")

    @property
    def internal_gossip_endpoints(self) -> list[str] | None:
        """Return internal gossip endpoints from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("internal_gossip_endpoints")

    @property
    def external_gossip_endpoints(self) -> list[str] | None:
        """Return external gossip endpoints from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("external_gossip_endpoints")

    @property
    def internal_http_endpoint(self) -> str | None:
        """Return internal http endpoint from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("internal_http_endpoint")

    @property
    def external_http_endpoint(self) -> str | None:
        """Return external http endpoint from provider app data."""
        data = self._get_app_databag_from_relation()
        return data.get("external_http_endpoint")


class ClusterEndpointsRequestEvent(RelationEvent):
    """Consul cluster endpoints request event."""

    pass


class ConsulServiceProviderEvents(ObjectEvents):
    """Events class for `on`."""

    endpoints_request = EventSource(ClusterEndpointsRequestEvent)


class ConsulServiceProvider(Object):
    """Class to be instantiated on the provider side of the relation."""

    on = ConsulServiceProviderEvents()  # pyright: ignore

    def __init__(self, charm: CharmBase, relation_name: str = DEFAULT_RELATION_NAME):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

        events = self.charm.on[relation_name]
        self.framework.observe(events.relation_changed, self._on_relation_changed)

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle new cluster client connect."""
        self.on.endpoints_request.emit(event.relation)

    def set_cluster_endpoints(
        self,
        relation: Relation | None,
        datacenter: str,
        internal_gossip_endpoints: list[str] | None,
        external_gossip_endpoints: list[str] | None,
        internal_http_endpoint: str | None,
        external_http_endpoint: str | None,
    ) -> None:
        """Set consul cluster endpoints on the relation.

        If relation is None, send cluster endpoints on all related units.
        """
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set endpoints")
            return

        try:
            databag = ConsulServiceProviderAppData(
                datacenter=datacenter,
                internal_gossip_endpoints=internal_gossip_endpoints,
                external_gossip_endpoints=external_gossip_endpoints,
                internal_http_endpoint=internal_http_endpoint,
                external_http_endpoint=external_http_endpoint,
            )
        except ValidationError as e:
            logger.info(f"Provider trying to set incorrect app data {str(e)}")
            return

        # If relation is not provided send endpoints to all the related
        # applications. This happens usually when endpoints data is
        # updated by provider and wants to send the data to all
        # related applications
        _datacenter: str = databag.datacenter
        _internal_gossip_endpoints: str = json.dumps(databag.internal_gossip_endpoints)
        _external_gossip_endpoints: str = json.dumps(databag.external_gossip_endpoints)
        _internal_http_endpoint: str = json.dumps(databag.internal_http_endpoint)
        _external_http_endpoint: str = json.dumps(external_http_endpoint)

        if relation is None:
            logging.debug(
                "Sending endpoints to all related applications of relation" f"{self.relation_name}"
            )
            relations_to_send_endpoints = self.framework.model.relations[self.relation_name]
        else:
            logging.debug(
                f"Sending endpoints on relation {relation.app.name} "
                f"{relation.name}/{relation.id}"
            )
            relations_to_send_endpoints = [relation]

        for relation in relations_to_send_endpoints:
            if relation:
                relation.data[self.charm.app]["datacenter"] = _datacenter
                relation.data[self.charm.app]["internal_gossip_endpoints"] = (
                    _internal_gossip_endpoints
                )
                relation.data[self.charm.app]["external_gossip_endpoints"] = (
                    _external_gossip_endpoints
                )
                relation.data[self.charm.app]["internal_http_endpoint"] = _internal_http_endpoint
                relation.data[self.charm.app]["external_http_endpoint"] = _external_http_endpoint
