# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import jsonschema
import logging
from typing import Optional

import ops
from ops.framework import EventBase, EventSource, Object, ObjectEvents


logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "0000a564247f4e07bdd5bf7820033167"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

CORS_ORIGIN_PROVIDER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "properties": {
        "origin": {
            "type": "string",
            "pattern": r"^https?://[^/]+$",
        }
    },
    "required": ["origin"],
}


class CORSOriginChangedEvent(EventBase):
    """Emitted when the Horizon public origin changes."""

    def __init__(self, handle, origin: str = ""):
        super().__init__(handle)
        self.origin = origin

    def snapshot(self):
        return {"origin": self.origin}

    def restore(self, snapshot):
        self.origin = snapshot["origin"]


class CORSOriginRequirerEvents(ObjectEvents):
    """Events emitted by the requirer side."""

    cors_origin_changed = EventSource(CORSOriginChangedEvent)


class CORSOriginProvider(Object):
    """Publish public origin."""

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

    def set_provider_info(self, origin: str) -> None:

        if not self._charm.unit.is_leader():
            return

        data = {
            "origin": origin,
        }

        jsonschema.validate(
            data,
            CORS_ORIGIN_PROVIDER_JSON_SCHEMA,
        )

        for relation in self._charm.model.relations[self._relation_name]:
            relation.data[self._charm.app].update(data)


class CORSOriginRequirer(Object):
    """Consume the public origin from relation."""

    on = CORSOriginRequirerEvents()

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
    ):
        super().__init__(charm, relation_name)

        self._charm = charm
        self._relation_name = relation_name

        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

        self.framework.observe(
            charm.on[relation_name].relation_broken,
            self._on_relation_broken,
        )

    def _on_relation_changed(
        self,
        event: ops.RelationChangedEvent,
    ) -> None:
        """Handle relation-changed."""

        self.on.cors_origin_changed.emit(
            origin=(
                self.get_horizon_origin(
                    relation_id=event.relation.id,
                )
                or ""
            )
        )

    def _on_relation_broken(
        self,
        event: ops.RelationBrokenEvent,
    ) -> None:
        """Handle relation-broken."""

        self.on.cors_origin_changed.emit(
            origin="",
        )

    def get_horizon_origin(
        self,
        relation_id: Optional[int] = None,
    ) -> Optional[str]:
        """Return the origin from relation data."""

        relations = (
            [
                self._charm.model.get_relation(
                    self._relation_name,
                    relation_id,
                )
            ]
            if relation_id
            else self._charm.model.relations[self._relation_name]
        )

        for relation in relations or []:
            if relation is None or relation.app is None:
                continue

            origin = relation.data[relation.app].get("origin")
            if origin:
                return origin

        return None