# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""DesignateServiceProvides and Requires module.

This library contains the Requires and Provides classes for handling
the designate interface.

Import `DesignateServiceRequires` in your charm, with the charm object and the
relation name:
    - self
    - "designate"

Two events are also available to respond to:
    - endpoint_changed
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.designate_k8s.v0.designate_service import (
    DesignateServiceRequires
)

class DesignateServiceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # DesignateService Requires
        self.designate_service = DesignateServiceRequires(
            self, "designate",
        )
        self.framework.observe(
            self.designate_service.on.endpoint_changed,
            self._on_designate_service_endpoint_changed
        )
        self.framework.observe(
            self.designate_service.on.goneaway,
            self._on_designate_service_goneaway
        )

    def _on_designate_service_endpoint_changed(self, event):
        '''React to the Designate service endpoint changed event.

        This event happens when DesignateService relation is added to the
        model and relation data is changed.
        '''
        # Do something with the configuration provided by relation.
        pass

    def _on_designate_service_goneaway(self, event):
        '''React to the DesignateService goneaway event.

        This event happens when DesignateService relation is removed.
        '''
        # DesignateService Relation has goneaway.
        pass
```
"""

import logging

import ops

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "3e0a3ac75f6d46a4ac5e144bbeb357e0"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class DesignateEndpointRequestEvent(ops.RelationEvent):
    """DesignateEndpointRequest Event."""

    pass


class DesignateServiceProviderEvents(ops.ObjectEvents):
    """Events class for `on`."""

    endpoint_request = ops.EventSource(DesignateEndpointRequestEvent)


class DesignateServiceProvides(ops.Object):
    """Class to be instantiated by the providing side of the relation."""

    on = DesignateServiceProviderEvents()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: ops.RelationChangedEvent):
        self.on.endpoint_request.emit(event.relation)

    def set_endpoint(
        self, relation: ops.Relation | None, endpoint: str
    ) -> None:
        """Set designate endpoint on the relation."""
        if not self.charm.unit.is_leader():
            logging.debug("Not a leader unit, skipping setting endpoint")
            return

        # If relation is not provided send endpoint to all the related
        # applications. This happens usually when endpoint data is
        # updated by provider and wants to send the data to all
        # related applications
        if relation is None:
            logging.debug(
                "Sending endpoint to all related applications of relation"
                f"{self.relation_name}"
            )
            for relation in self.framework.model.relations[self.relation_name]:
                relation.data[self.charm.app]["endpoint"] = endpoint
        else:
            logging.debug(
                f"Sending endpoint on relation {relation.app.name} "
                f"{relation.name}/{relation.id}"
            )
            relation.data[self.charm.app]["endpoint"] = endpoint


class DesignateEndpointChangedEvent(ops.RelationEvent):
    """DesignateEndpointChanged Event."""

    pass


class DesignateServiceGoneAwayEvent(ops.RelationEvent):
    """DesignateServiceGoneAway Event."""

    pass


class DesignateServiceRequirerEvents(ops.ObjectEvents):
    """Events class for `on`."""

    endpoint_changed = ops.EventSource(DesignateEndpointChangedEvent)
    goneaway = ops.EventSource(DesignateServiceGoneAwayEvent)


class DesignateServiceRequires(ops.Object):
    """Class to be instantiated by the requiring side of the relation."""

    on = DesignateServiceRequirerEvents()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
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

    def _on_relation_changed(self, event: ops.RelationJoinedEvent):
        """Handle relation changed event."""
        self.on.endpoint_changed.emit(event.relation)

    def _on_relation_broken(self, event: ops.RelationBrokenEvent):
        """Handle relation broken event."""
        self.on.goneaway.emit(event.relation)

    @property
    def _designate_service_rel(self) -> ops.Relation | None:
        """The designate service relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> str | None:
        """Return the value for the given key from remote app data."""
        if self._designate_service_rel:
            data = self._designate_service_rel.data[
                self._designate_service_rel.app
            ]
            return data.get(key)

        return None

    @property
    def endpoint(self) -> str | None:
        """Return the designate endpoint."""
        return self.get_remote_app_data("endpoint")
