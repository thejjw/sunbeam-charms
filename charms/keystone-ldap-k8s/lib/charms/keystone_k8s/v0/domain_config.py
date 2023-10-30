"""Interface for passing domain configuration."""

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
import base64
logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "dfeee73ed0b248c29ed905aeda6fd417"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

class DomainConfigRequestEvent(RelationEvent):
    """DomainConfigRequest Event."""
    pass

class DomainConfigProviderEvents(ObjectEvents):
    """Events class for `on`."""

    remote_ready = EventSource(DomainConfigRequestEvent)

class DomainConfigProvides(Object):
    """DomainConfigProvides class."""

    on = DomainConfigProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_domain_config_relation_changed,
        )

    def _on_domain_config_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle DomainConfig relation changed."""
        logging.debug("DomainConfig relation changed")
        self.on.remote_ready.emit(event.relation)

    def set_domain_info(
        self, domain_name: str, config_contents: str, ca=None
    ) -> None:
        """Set ceilometer configuration on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping set config")
            return
        for relation in self.relations:
            relation.data[self.charm.app]["domain-name"] = domain_name
            relation.data[self.charm.app]["config-contents"] = base64.b64encode(config_contents.encode()).decode()
            if ca:
                relation.data[self.charm.app]["ca"] = base64.b64encode(ca.encode()).decode()

    @property
    def relations(self):
        return self.framework.model.relations[self.relation_name]

class DomainConfigChangedEvent(RelationEvent):
    """DomainConfigChanged Event."""

    pass


class DomainConfigGoneAwayEvent(RelationBrokenEvent):
    """DomainConfigGoneAway Event."""

    pass


class DomainConfigRequirerEvents(ObjectEvents):
    """Events class for `on`."""

    config_changed = EventSource(DomainConfigChangedEvent)
    goneaway = EventSource(DomainConfigGoneAwayEvent)


class DomainConfigRequires(Object):
    """DomainConfigRequires class."""

    on = DomainConfigRequirerEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_domain_config_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_domain_config_relation_broken,
        )

    def _on_domain_config_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle DomainConfig relation changed."""
        logging.debug("DomainConfig config data changed")
        self.on.config_changed.emit(event.relation)

    def _on_domain_config_relation_broken(
        self, event: RelationBrokenEvent
    ):
        """Handle DomainConfig relation changed."""
        logging.debug("DomainConfig on_broken")
        self.on.goneaway.emit(event.relation)

    def get_domain_configs(self, exclude=None):
        exclude = exclude or []
        configs = []
        for relation in self.relations:
            if relation in exclude:
                continue
            try:
                domain_name = relation.data[relation.app].get("domain-name")
            except KeyError:
                logging.debug("Key error accessing app data")
                continue
            raw_config_contents = relation.data[relation.app].get("config-contents")
            if not all([domain_name, raw_config_contents]):
                continue
            raw_ca = relation.data[relation.app].get("ca")
            config = {
                "domain-name": domain_name,
                "config-contents": base64.b64decode(raw_config_contents).decode()}
            if raw_ca:
                config["ca"] = base64.b64decode(raw_ca).decode()
            configs.append(config)
        return configs

    @property
    def relations(self):
        return self.framework.model.relations[self.relation_name]

