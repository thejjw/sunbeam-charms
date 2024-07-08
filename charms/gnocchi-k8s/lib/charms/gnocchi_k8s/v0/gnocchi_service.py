"""GnocchiService Provides and Requires module.

This library contains the Requires and Provides classes for handling
the Gnocchi service interface.

Import `GnocchiServiceRequires` in your charm, with the charm object and the
relation name:
    - self
    - "gnocchi-db"

Two events are also available to respond to:
    - readiness_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.gnocchi_k8s.v0.gnocchi_service import (
    GnocchiServiceRequires
)

class GnocchiServiceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        #  GnocchiService Requires
        self.gnocchi_svc = GnocchiServiceRequires(
            self, "gnocchi-db",
        )
        self.framework.observe(
            self.gnocchi_svc.on.readiness_changed,
            self._on_gnocchi_service_readiness_changed
        )
        self.framework.observe(
            self.gnocchi_svc.on.goneaway,
            self._on_gnocchi_service_goneaway
        )

    def _on_gnocchi_service_readiness_changed(self, event):
        '''React to the Gnocchi service readiness changed event.

        This event happens when Gnocchi service relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_gnocchi_service_goneaway(self, event):
        '''React to the Gnocchi Service goneaway event.

        This event happens when Gnocchi service relation is removed.
        '''
        # HeatSharedConfig Relation has goneaway.
        pass
```
"""


import json
import logging
from typing import (
    Optional,
)

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

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "97b7682b415040f3b32d77fff8d93e7e"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2


class GnocchiServiceReadinessRequestEvent(RelationEvent):
    """GnocchiServiceReadinessRequest Event."""

    pass


class GnocchiServiceProviderEvents(ObjectEvents):
    """Events class for `on`."""

    service_readiness = EventSource(GnocchiServiceReadinessRequestEvent)


class GnocchiServiceProvides(Object):
    """GnocchiServiceProvides class."""

    on = GnocchiServiceProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle Gnocchi service relation changed."""
        logging.debug("Gnocchi Service relation changed")
        self.on.service_readiness.emit(event.relation)

    def set_service_status(self, relation: Relation, is_ready: bool) -> None:
        """Set gnocchi service readiness status on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping setting ready status")
            return

        logging.debug(
            f"Setting ready status on relation {relation.app.name} "
            f"{relation.name}/{relation.id}"
        )
        relation.data[self.charm.app]["ready"] = json.dumps(is_ready)


class GnocchiServiceReadinessChangedEvent(RelationEvent):
    """GnocchiServiceReadinessChanged Event."""

    pass


class GnocchiServiceGoneAwayEvent(RelationEvent):
    """GnocchiServiceGoneAway Event."""

    pass


class GnocchiServiceRequirerEvents(ObjectEvents):
    """Events class for `on`."""

    readiness_changed = EventSource(GnocchiServiceReadinessChangedEvent)
    goneaway = EventSource(GnocchiServiceGoneAwayEvent)


class GnocchiServiceRequires(Object):
    """GnocchiServiceRequires class."""

    on = GnocchiServiceRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_relation_broken,
        )

    def _on_relation_changed(self, event: RelationChangedEvent):
        """Handle Gnocchi Service relation changed."""
        logging.debug("Gnocchi service readiness data changed")
        self.on.readiness_changed.emit(event.relation)

    def _on_relation_broken(self, event: RelationBrokenEvent):
        """Handle Gnocchi Service relation broken."""
        logging.debug("Gnocchi service on_broken")
        self.on.goneaway.emit(event.relation)

    @property
    def _gnocchi_service_rel(self) -> Optional[Relation]:
        """The gnocchi service relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> Optional[str]:
        """Return the value for the given key from remote app data."""
        if self._gnocchi_service_rel:
            data = self._gnocchi_service_rel.data[
                self._gnocchi_service_rel.app
            ]
            return data.get(key)

        return None

    @property
    def service_ready(self) -> bool:
        """Return if gnocchi service is ready or not."""
        is_ready = self.get_remote_app_data("ready")
        if is_ready:
            return json.loads(is_ready)

        return False
