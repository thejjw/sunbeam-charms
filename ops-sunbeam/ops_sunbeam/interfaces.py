# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Common interfaces not charm specific."""

import logging
import typing

import ops.model
from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)
from ops_sunbeam.core import (
    RelationDataMapping,
)


class PeersRelationCreatedEvent(EventBase):
    """The PeersRelationCreatedEvent indicates that peer relation now exists.

    It does not indicate that any peers are available or have joined, simply
    that the relation exists. This is useful to to indicate that the
    application databag is available for storing information shared across
    units.
    """

    pass


class PeersDataChangedEvent(EventBase):
    """The PeersDataChangedEvent indicates peer data hjas changed."""

    pass


class PeersRelationJoinedEvent(EventBase):
    """The PeersRelationJoinedEvent indicates a new unit has joined."""

    pass


class PeersEvents(ObjectEvents):
    """Peer Events."""

    peers_relation_created = EventSource(PeersRelationCreatedEvent)
    peers_relation_joined = EventSource(PeersRelationJoinedEvent)
    peers_data_changed = EventSource(PeersDataChangedEvent)


class OperatorPeers(Object):
    """Interface for the peers relation."""

    on = PeersEvents()  # type: ignore
    state = StoredState()

    def __init__(self, charm: ops.charm.CharmBase, relation_name: str) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name)
        self.relation_name = relation_name
        self.framework.observe(
            charm.on[relation_name].relation_created, self.on_created
        )
        self.framework.observe(
            charm.on[relation_name].relation_joined, self.on_joined
        )
        self.framework.observe(
            charm.on[relation_name].relation_changed, self.on_changed
        )

    @property
    def peers_rel(self) -> ops.model.Relation | None:
        """Peer relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def _app_data_bag(self) -> typing.MutableMapping[str, str]:
        """Return all app data on peer relation."""
        if not self.peers_rel:
            return {}
        return self.peers_rel.data[self.peers_rel.app]

    def on_joined(self, event: ops.EventBase) -> None:
        """Handle relation joined event."""
        logging.info("Peer joined")
        self.on.peers_relation_joined.emit()

    def on_created(self, event: ops.EventBase) -> None:
        """Handle relation created event."""
        logging.info("Peers on_created")
        self.on.peers_relation_created.emit()

    def on_changed(self, event: ops.EventBase) -> None:
        """Handle relation changed event."""
        logging.info("Peers on_changed")
        self.on.peers_data_changed.emit()

    def set_app_data(self, settings: RelationDataMapping) -> None:
        """Publish settings on the peer app data bag."""
        for k, v in settings.items():
            self._app_data_bag[k] = v

    def get_app_data(self, key: str) -> str | None:
        """Get the value corresponding to key from the app data bag."""
        if not self.peers_rel:
            return None
        return self._app_data_bag.get(key)

    def get_all_app_data(self) -> typing.MutableMapping[str, str]:
        """Return all the app data from the relation."""
        return self._app_data_bag

    def get_all_unit_values(
        self, key: str, include_local_unit: bool = False
    ) -> list[str]:
        """Retrieve value for key from all related units.

        :param include_local_unit: Include value set by local unit
        """
        values: list[str] = []
        if not self.peers_rel:
            return values
        for unit in self.peers_rel.units:
            value = self.peers_rel.data[unit].get(key)
            if value is not None:
                values.append(value)
        local_unit_value = self.peers_rel.data[self.model.unit].get(key)
        if include_local_unit and local_unit_value:
            values.append(local_unit_value)
        return values

    def set_unit_data(self, settings: typing.Mapping[str, str]) -> None:
        """Publish settings on the peer unit data bag."""
        if not self.peers_rel:
            return
        for k, v in settings.items():
            self.peers_rel.data[self.model.unit][k] = v

    def get_local_unit_value(self, key: str) -> str | None:
        """Return the value for key from the local unit's peer unit databag."""
        if not self.peers_rel:
            return None
        return self.peers_rel.data[self.model.unit].get(key)

    def all_joined_units(self) -> set[ops.model.Unit]:
        """All remote units joined to the peer relation."""
        if not self.peers_rel:
            return set()
        return set(self.peers_rel.units)

    def expected_peer_units(self) -> int:
        """Return the Number of units expected on relation.

        NOTE: This count includes this unit
        """
        if not self.peers_rel:
            return 0
        return self.peers_rel.app.planned_units()
