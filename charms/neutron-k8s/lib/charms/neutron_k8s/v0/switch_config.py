"""Neutron switch-config Provides and Requires module.

This library contains the Requires and Provides classes for handling
the switch-config interface.

Import `SwitchConfigRequires` in your charm, with the charm object and the
relation name:
    - self
    - "switch-config"

Two events are also available to respond to:
    - connected
    - goneaway

A basic example showing the usage of this relation follows:

```
import charms.neutron_k8s.v0.switch_config as switch_config


class SwitchConfigCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # switch-config Requires
        self._switch_config = switch_config.SwitchConfigRequires(
            self, "switch-config",
        )
        self.framework.observe(
            self._switch_config.on.connected,
            self._on_switch_config_connected,
        )
        self.framework.observe(
            self._switch_config.on.goneaway,
            self._on_switch_config_goneaway,
        )


    def _on_switch_config_connected(self, event):
        '''React to the SwitchConfigConnectedEvent event.

        This event happens when the switch-config relation is added to the
        model.
        '''
        # switch-config relation has been added. Reconfigure services as needed.
        pass

    def _on_switch_config_goneaway(self, event):
        '''React to the SwitchConfigGoneAwayEvent event.

        This event happens when switch-config relation is removed.
        '''
        # switch-config relation has goneaway. Shutdown services if needed.
        pass
```
"""

import logging
from typing import (
    Dict,
    List,
)

from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
    RelationJoinedEvent,
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
LIBID = "ce4db51390054e1f953f19997fa62dda"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

SWITCH_CONFIG = "switch-config"


class SwitchConfigConnectedEvent(RelationEvent):
    """switch-config connected event."""

    pass


class SwitchConfigGoneAwayEvent(RelationEvent):
    """switch-config relation has gone-away event."""

    pass


class SwitchConfigProvidesEvents(ObjectEvents):
    """Events class for `on`."""

    switch_config_connected = EventSource(SwitchConfigConnectedEvent)


class SwitchConfigRequiresEvents(ObjectEvents):
    """Events class for `on`."""

    switch_config_connected = EventSource(SwitchConfigConnectedEvent)
    switch_config_goneaway = EventSource(SwitchConfigGoneAwayEvent)


class SwitchConfigProvides(Object):
    """SwitchConfigProvides class."""

    on = SwitchConfigProvidesEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_switch_config_relation_joined,
        )

    def _on_switch_config_relation_joined(self, event: RelationJoinedEvent):
        """Handle switch-config relation joined."""
        logging.debug("switch-config relation joined")
        self.on.switch_config_connected.emit(event.relation)

    @property
    def _switch_config_rel(self) -> Relation | None:
        """The switch-config relation."""
        return self.framework.model.get_relation(self.relation_name)

    def update_switch_configs(self, configs: str | None):
        """Updates the configs in the switch-config relation."""
        rel = self._switch_config_rel
        if not rel:
            return

        rel_data = rel.data[self.model.app]
        if configs:
            rel_data[SWITCH_CONFIG] = configs
        else:
            rel_data.pop(SWITCH_CONFIG, None)


class SwitchConfigRequires(Object):
    """SwitchConfigRequires class."""

    on = SwitchConfigRequiresEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_switch_config_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_switch_config_relation_broken,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_switch_config_relation_broken,
        )

    def _on_switch_config_relation_changed(self, event: RelationChangedEvent):
        """Handle switch-config relation changed."""
        logging.debug("switch-config relation changed")
        self.on.switch_config_connected.emit(event.relation)

    def _on_switch_config_relation_broken(self, event: RelationBrokenEvent):
        """Handle switch-config relation broken."""
        logging.debug("switch-config relation broken")
        self.on.switch_config_goneaway.emit(event.relation)

    @property
    def _switch_config_rel(self) -> Relation | None:
        """The switch-config relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def switch_configs(self) -> List[dict]:
        """Get the configs from the switch-config relation."""
        rel = self._switch_config_rel
        if not rel:
            return []

        rel_data = rel.data[rel.app]
        if SWITCH_CONFIG not in rel_data:
            return []

        juju_secrets = rel_data[SWITCH_CONFIG]

        configs = []
        for secret_id in juju_secrets.split(","):
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content()
            configs.append(content)

        return configs
