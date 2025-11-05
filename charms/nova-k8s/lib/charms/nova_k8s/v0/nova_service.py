"""NovaServiceProvides and Requires module.

This library contains the Requires and Provides classes for handling
the nova_service interface.

Import `NovaServiceRequires` in your charm, with the charm object and the
relation name:
    - self
    - "nova_service"

Two events are also available to respond to:
    - config_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.nova_k8s.v0.nova_service import (
    NovaServiceRequires
)

class NovaServiceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # NovaService Requires
        self.nova_service = NovaServiceRequires(
            self, "nova_service",
        )
        self.framework.observe(
            self.nova_service.on.config_changed,
            self._on_nova_service_config_changed
        )
        self.framework.observe(
            self.nova_service.on.goneaway,
            self._on_nova_service_goneaway
        )

    def _on_nova_service_config_changed(self, event):
        '''React to the Nova service config changed event.

        This event happens when NovaService relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_nova_service_goneaway(self, event):
        '''React to the NovaService goneaway event.

        This event happens when NovaService relation is removed.
        '''
        # NovaService Relation has goneaway.
        pass
```
"""

import logging

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
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
LIBID = "050da1b56a094b52a08bb9b9ab7504f1"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2


class NovaConfigRequestEvent(RelationEvent):
    """NovaConfigRequest Event."""

    pass


class NovaServiceProviderEvents(ObjectEvents):
    """Events class for `on`."""

    config_request = EventSource(NovaConfigRequestEvent)


class NovaServiceProvides(Object):
    """NovaServiceProvides class."""

    on = NovaServiceProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_nova_service_relation_changed,
        )

    def _on_nova_service_relation_changed(self, event: RelationChangedEvent):
        """Handle NovaService relation changed."""
        logging.debug("NovaService relation changed")
        self.on.config_request.emit(event.relation)

    def set_config(
        self,
        relation: Relation | None,
        nova_spiceproxy_url: str,
        pci_aliases: str,
        region: str,
    ) -> None:
        """Set nova configuration on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set config")
            return

        # If relation is not provided send config to all the related
        # applications. This happens usually when config data is
        # updated by provider and wants to send the data to all
        # related applications
        relation_data_updates = {
            "spice-proxy-url": nova_spiceproxy_url or "",
            "pci-aliases": pci_aliases or "",
            "region": region or "",
        }
        if relation is None:
            logging.debug(
                "Sending config to all related applications of relation"
                f"{self.relation_name}"
            )
            for relation in self.framework.model.relations[self.relation_name]:
                relation.data[self.charm.app].update(relation_data_updates)
        else:
            logging.debug(
                f"Sending config on relation {relation.app.name} "
                f"{relation.name}/{relation.id}"
            )
            relation.data[self.charm.app].update(relation_data_updates)


class NovaConfigChangedEvent(RelationEvent):
    """NovaConfigChanged Event."""

    pass


class NovaServiceGoneAwayEvent(RelationEvent):
    """NovaServiceGoneAway Event."""

    pass


class NovaServiceRequirerEvents(ObjectEvents):
    """Events class for `on`."""

    config_changed = EventSource(NovaConfigChangedEvent)
    goneaway = EventSource(NovaServiceGoneAwayEvent)


class NovaServiceRequires(Object):
    """NovaServiceRequires class."""

    on = NovaServiceRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_nova_service_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_nova_service_relation_broken,
        )

    def _on_nova_service_relation_changed(self, event: RelationChangedEvent):
        """Handle NovaService relation changed."""
        logging.debug("NovaService config data changed")
        self.on.config_changed.emit(event.relation)

    def _on_nova_service_relation_broken(self, event: RelationBrokenEvent):
        """Handle NovaService relation changed."""
        logging.debug("NovaService on_broken")
        self.on.goneaway.emit(event.relation)

    @property
    def _nova_service_rel(self) -> Relation | None:
        """The nova service relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> str | None:
        """Return the value for the given key from remote app data."""
        if self._nova_service_rel:
            data = self._nova_service_rel.data[self._nova_service_rel.app]
            return data.get(key)

        return None

    @property
    def nova_spiceproxy_url(self) -> str | None:
        """Return the nova_spiceproxy url."""
        return self.get_remote_app_data("spice-proxy-url")

    @property
    def pci_aliases(self) -> str | None:
        """Return pci aliases."""
        return self.get_remote_app_data("pci-aliases")

    @property
    def region(self) -> str | None:
        """Return the region of the Nova API service."""
        return self.get_remote_app_data("region")
