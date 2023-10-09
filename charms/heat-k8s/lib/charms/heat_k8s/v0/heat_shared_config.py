"""HeatSharedConfig Provides and Requires module.

This library contains the Requires and Provides classes for handling
the heat-shared-config interface.

Import `HeatSharedConfigRequires` in your charm, with the charm object and the
relation name:
    - self
    - "heat_config"

Two events are also available to respond to:
    - config_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.heat_k8s.v0.heat_shared_config import (
    HeatSharedConfigRequires
)

class HeatSharedConfigClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        #  HeatSharedConfig Requires
        self.heat_config = HeatSharedConfigRequires(
            self, "heat_config",
        )
        self.framework.observe(
            self.heat_config.on.config_changed,
            self._on_heat_shared_config_changed
        )
        self.framework.observe(
            self.heat_config.on.goneaway,
            self._on_heat_shared_config_goneaway
        )

    def _on_heat_shared_config_changed(self, event):
        '''React to the Heat shared config changed event.

        This event happens when Heat shared config relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_heat_shared_config_goneaway(self, event):
        '''React to the HeatSharedConfig goneaway event.

        This event happens when Heat shared config relation is removed.
        '''
        # HeatSharedConfig Relation has goneaway.
        pass
```
"""

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
LIBID = "88823d2312d34be08ba8940b3b30c3d4"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class HeatSharedConfigRequestEvent(RelationEvent):
    """HeatConfigRequest Event."""

    pass


class HeatSharedConfigProviderEvents(ObjectEvents):
    """Events class for `on`."""

    config_request = EventSource(HeatSharedConfigRequestEvent)


class HeatSharedConfigProvides(Object):
    """HeatSharedConfigProvides class."""

    on = HeatSharedConfigProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_heat_shared_config_relation_changed,
        )

    def _on_heat_shared_config_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle HeatSharedConfig relation changed."""
        logging.debug("HeatSharedConfig relation changed")
        self.on.config_request.emit(event.relation)

    def set_config(
        self, relation: Relation, auth_encryption_key: str
    ) -> None:
        """Set heat configuration on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set config")
            return

        logging.debug(
            f"Sending config on relation {relation.app.name} "
            f"{relation.name}/{relation.id}"
        )
        relation.data[self.charm.app][
            "auth-encryption-key"
        ] = auth_encryption_key


class HeatSharedConfigChangedEvent(RelationEvent):
    """HeatSharedConfigChanged Event."""

    pass


class HeatSharedConfigGoneAwayEvent(RelationEvent):
    """HeatSharedConfigGoneAway Event."""

    pass


class HeatSharedConfigRequirerEvents(ObjectEvents):
    """Events class for `on`."""

    config_changed = EventSource(HeatSharedConfigChangedEvent)
    goneaway = EventSource(HeatSharedConfigGoneAwayEvent)


class HeatSharedConfigRequires(Object):
    """HeatSharedConfigRequires class."""

    on = HeatSharedConfigRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_heat_shared_config_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_heat_shared_config_relation_broken,
        )

    def _on_heat_shared_config_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle HeatSharedConfig relation changed."""
        logging.debug("HeatSharedConfig config data changed")
        self.on.config_changed.emit(event.relation)

    def _on_heat_shared_config_relation_broken(
        self, event: RelationBrokenEvent
    ):
        """Handle HeatSharedConfig relation changed."""
        logging.debug("HeatSharedConfig on_broken")
        self.on.goneaway.emit(event.relation)

    @property
    def _heat_shared_config_rel(self) -> Optional[Relation]:
        """The heat shared config relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> Optional[str]:
        """Return the value for the given key from remote app data."""
        if self._heat_shared_config_rel:
            data = self._heat_shared_config_rel.data[
                self._heat_shared_config_rel.app
            ]
            return data.get(key)

        return None

    @property
    def auth_encryption_key(self) -> Optional[str]:
        """Return the auth_encryption_key."""
        return self.get_remote_app_data("auth-encryption-key")
