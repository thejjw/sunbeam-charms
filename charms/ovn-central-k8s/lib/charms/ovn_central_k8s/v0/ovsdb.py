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

import json
import logging
import typing

import ops
from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)

# The unique Charmhub library identifier, never change it
LIBID = "114b7bb1970445daa61650e451f9da62"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 5


LOADBALANCER_KEY = "loadbalancer-address"
EXTERNAL_KEY = "external-connectivity"
PROXY_KEY = "proxy-relation"


# TODO: add your code here! Happy coding!
class OVSDBCMSConnectedEvent(EventBase):
    """OVSDBCMS connected Event."""

    pass


class OVSDBCMSReadyEvent(EventBase):
    """OVSDBCMS ready for use Event."""

    pass


class OVSDBCMSGoneAwayEvent(EventBase):
    """OVSDBCMS relation has gone-away Event"""

    pass


class OVSDBCMSServerEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(OVSDBCMSConnectedEvent)
    ready = EventSource(OVSDBCMSReadyEvent)
    goneaway = EventSource(OVSDBCMSGoneAwayEvent)


class OVSDBCMSRequires(Object):
    """
    OVSDBCMSRequires class

    The OVSDBCMSRequires class handles the requires side of the
    ovsdb-cms relation.

    This class can support vanilla, proxied and loadbalancer-based
    connections. The order of preference is:
    1. Proxied connections (if proxy-relation is set to true by the provider)
    2. Loadbalancer-based connections (if loadbalancer-address is provided
       by the provider)
    3. Direct connections (if bound-hostname or bound-address are provided
       by the provider)
    """

    on = OVSDBCMSServerEvents()
    _stored = StoredState()

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        external_connectivity: bool = False,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.external_connectivity = external_connectivity
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_ovsdb_cms_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ovsdb_cms_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_ovsdb_cms_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ovsdb_cms_relation_broken,
        )
        self.request_access(external_connectivity)

    def request_access(self, external_connectivity: bool) -> None:
        """Request access to the external connectivity."""
        if self.model.unit.is_leader():
            relation = self.model.get_relation(self.relation_name)
            if relation:
                relation.data[self.model.app][EXTERNAL_KEY] = json.dumps(
                    external_connectivity
                )

    def _on_ovsdb_cms_relation_joined(self, event):
        """OVSDBCMS relation joined."""
        logging.debug("OVSDBCMSRequires on_joined")
        self.on.connected.emit()

    def bound_hostnames(self):
        return self.get_all_unit_values("bound-hostname")

    def bound_addresses(self):
        return self.get_all_unit_values("bound-address")

    def loadbalancer_address(self) -> str | None:
        relation = self.model.get_relation(self.relation_name)
        if relation:
            return relation.data[relation.app].get(LOADBALANCER_KEY)
        return None

    def proxied_connection_strings(self) -> dict[str, str]:
        """Return proxied relationdata connection strings."""
        relation = self.model.get_relation(self.relation_name)
        if not relation:
            return {}
        if self.remote_proxied():
            return {
                "nb": relation.data[relation.app].get("db_nb_connection_strs", ""),
                "sb": relation.data[relation.app].get("db_sb_connection_strs", ""),
            }
        return {}

    def remote_proxied(self) -> bool:
        relation = self.model.get_relation(self.relation_name)
        if relation:
            proxied = relation.data[relation.app].get(PROXY_KEY)
            return proxied == "true"
        return False

    def remote_ready(self) -> bool:
        """Whether the remote side is ready."""
        if self.remote_proxied():
            return all(self.proxied_connection_strings().values())
        if self.external_connectivity:
            return self.loadbalancer_address() is not None
        return all(self.bound_hostnames()) or all(self.bound_addresses())

    def _on_ovsdb_cms_relation_changed(self, event):
        """OVSDBCMS relation changed."""
        logging.debug("OVSDBCMSRequires on_changed")
        if self.remote_ready():
            self.on.ready.emit()

    def _on_ovsdb_cms_relation_broken(self, event):
        """OVSDBCMS relation broken."""
        logging.debug("OVSDBCMSRequires on_broken")
        self.on.goneaway.emit()

    def get_all_unit_values(self, key: str) -> typing.List[str]:
        """Retrieve value for key from all related units."""
        values = []
        relation = self.framework.model.get_relation(self.relation_name)
        if relation:
            for unit in relation.units:
                values.append(relation.data[unit].get(key))
        return values


class OVSDBCMSClientConnectedEvent(EventBase):
    """OVSDBCMS connected Event."""

    pass


class OVSDBCMSClientReadyEvent(EventBase):
    """OVSDBCMS ready for use Event."""

    pass


class OVSDBCMSClientGoneAwayEvent(EventBase):
    """OVSDBCMS relation has gone-away Event"""

    pass


class OVSDBCMSClientEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(OVSDBCMSClientConnectedEvent)
    ready = EventSource(OVSDBCMSClientReadyEvent)
    goneaway = EventSource(OVSDBCMSClientGoneAwayEvent)


class OVSDBCMSProvides(Object):
    """
    OVSDBCMSProvides class
    """

    on = OVSDBCMSClientEvents()
    _stored = StoredState()

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        loadbalancer_address: str | None = None,
        proxy_relation: bool = False,
    ):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.loadbalancer_address = loadbalancer_address
        self.proxy_relation = proxy_relation
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_ovsdb_cms_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_ovsdb_cms_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_ovsdb_cms_relation_broken,
        )
        self.update_relation_data()

    def update_relation_data(
        self
    ) -> None:
        """Update relation data."""
        if not self.model.unit.is_leader():
            return
        for rel in self.model.relations[self.relation_name]:
            app_data = rel.data[self.model.app]
            if self.proxy_relation:
                app_data[PROXY_KEY] = "true"
            if self.loadbalancer_address:
                app_data[LOADBALANCER_KEY] = self.loadbalancer_address

    def _on_ovsdb_cms_relation_joined(self, event):
        """Handle ovsdb-cms joined."""
        logging.debug("OVSDBCMSProvides on_joined")
        self.on.connected.emit()

    def _on_ovsdb_cms_relation_changed(self, event):
        """Handle ovsdb-cms changed."""
        logging.debug("OVSDBCMSProvides on_changed")
        self.on.ready.emit()

    def _on_ovsdb_cms_relation_broken(self, event):
        """Handle ovsdb-cms broken."""
        logging.debug("OVSDBCMSProvides on_departed")
        self.on.goneaway.emit()

    def set_unit_data(self, settings: typing.Dict[str, str]) -> None:
        """Publish settings on the peer unit data bag."""
        relations = self.framework.model.relations[self.relation_name]
        for relation in relations:
            for k, v in settings.items():
                relation.data[self.model.unit][k] = v

    def set_app_data(self, settings: typing.Dict[str, str]) -> None:
        """Publish settings on the peer application data bag."""
        if not self.model.unit.is_leader():
            return
        relations = self.framework.model.relations[self.relation_name]
        for relation in relations:
            for k, v in settings.items():
                relation.data[self.model.app][k] = v

    def clear_unit_data(self) -> None:
        """Clear all unit relation data for all relations."""
        for relation in self.model.relations[self.relation_name]:
            databag = relation.data[self.model.unit]
            databag.update(
                {key: '' for key in databag}
            )

    def clear_app_data(self) -> None:
        """Clear all application relation data for all relations."""
        if not self.model.unit.is_leader():
            return
        for relation in self.model.relations[self.relation_name]:
            databag = relation.data[self.model.app]
            databag.update(
                {key: '' for key in databag}
            )
