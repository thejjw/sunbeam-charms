# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

import jsonschema
from ops.charm import (
    CharmBase,
    RelationBrokenEvent,
    RelationChangedEvent,
)
from ops.framework import EventBase, EventSource, Handle, Object, ObjectEvents
from ops.model import Relation, TooManyRelatedAppsError

# The unique Charmhub library identifier, never change it
LIBID = "0cec5003349d4cac9adeb7dfc958d097"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

PYDEPS = ["jsonschema"]

DEFAULT_RELATION_NAME = "keystone-saml"
logger = logging.getLogger(__name__)

PROVIDER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "metadata": {
            "type": "string",
            "description": "The IDP metadata.",
        },
        "name": {
            "type": "string",
            "description": "The provider ID that will be used for this IDP.",
        },
        "label": {
            "type": "string",
            "description": "The label which will be used in the dashboard.",
        },
        "ca_chain": {
            "type": "array",
            "items": {
                "type": "string"
            },
            "default": [],
            "description": "A CA chain that the requirer needs in order to trust the IDP."
        },
    },
    "additionalProperties": False,
    "required": ["metadata", "name", "label"]
}

REQUIRER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "acs-url": {
            "type": "string",
            "description": "The assertion consumer service (acs) URL."
        },
        "logout-url": {
            "type": "string",
            "description": "The SP logout URL."
        },
        "metadata-url": {
            "type": "string",
            "description": "The metadata URL for the keystone SP."
        },
    },
    "additionalProperties": False,
    "required": ["acs-url", "logout-url", "metadata-url"]
}


class DataValidationError(RuntimeError):
    """Raised when data validation fails on relation data."""


def _validate_data(data: Dict, schema: Dict) -> None:
    """Checks whether `data` matches `schema`.

    Will raise DataValidationError if the data is not valid, else return None.
    """
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as e:
        raise DataValidationError(data, schema) from e


def _load_data(data: Mapping, schema: Optional[Dict] = None) -> Dict:
    """Parses nested fields and checks whether `data` matches `schema`."""
    ret = {}
    for k, v in data.items():
        try:
            ret[k] = json.loads(v)
        except json.JSONDecodeError:
            ret[k] = v

    if schema:
        _validate_data(ret, schema)
    return ret


def _dump_data(data: Dict, schema: Optional[Dict] = None) -> Dict:
    if schema:
        _validate_data(data, schema)

    ret = {}
    for k, v in data.items():
        if isinstance(v, (list, dict)):
            try:
                ret[k] = json.dumps(v)
            except json.JSONDecodeError as e:
                raise DataValidationError(f"Failed to encode relation json: {e}")
        elif isinstance(v, bool):
            ret[k] = str(v)
        else:
            ret[k] = v
    return ret


class KeystoneSAMLProviderChangedEvent(EventBase):
    """Event to notify the charm that the information in the databag changed."""

    def __init__(
        self, handle: Handle, acs_url: str, metadata_url: str, logout_url: str
    ):
        super().__init__(handle)
        self.acs_url = acs_url
        self.metadata_url = metadata_url
        self.logout_url = logout_url

    def snapshot(self) -> Dict:
        """Save event."""
        return {
            "acs_url": self.acs_url,
            "metadata_url": self.metadata_url,
            "logout_url": self.logout_url,
        }

    def restore(self, snapshot: Dict) -> None:
        """Restore event."""
        super().restore(snapshot)
        self.acs_url = snapshot["acs_url"]
        self.metadata_url = snapshot["metadata_url"]
        self.logout_url = snapshot["logout_url"]


class KeystoneSAMLProviderEvents(ObjectEvents):
    """Event descriptor for events raised by `KeystoneSAMLProviderEvents`."""

    changed = EventSource(KeystoneSAMLProviderChangedEvent)


class KeystoneSAMLProvider(Object):

    on = KeystoneSAMLProviderEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        events = self._charm.on[relation_name]
        self.framework.observe(
            events.relation_changed,
            self._on_relation_changed_event)
        self.framework.observe(
            events.relation_broken,
            self._on_relation_broken_event)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Handle relation changed event."""
        data = event.relation.data[event.app]
        if not data:
            logger.info("No requirer relation data available.")
            return

        try:
            data = _load_data(data, REQUIRER_JSON_SCHEMA)
        except DataValidationError as e:
            logger.info(f"failed to validate relation data: {e}")
            return

        self.on.changed.emit(
            acs_url=data["acs-url"],
            metadata_url=data["metadata-url"],
            logout_url=data["logout-url"],
        )

    def _on_relation_broken_event(self, event: RelationBrokenEvent) -> None:
        """Handle relation broken event."""
        logger.info("Relation broken, clearing keystone SP urls.")
        self.on.changed.emit(
            acs_url="", metadata_url="", logout_url=""
        )

    def set_provider_info(self, info: Mapping[str, str]) -> None:
        if not self.model.unit.is_leader():
            return

        _validate_data(info, PROVIDER_JSON_SCHEMA)

        encoded = base64.b64encode(info["metadata"].encode())
        info["metadata"] = encoded.decode()
        rel_data = _dump_data(info, PROVIDER_JSON_SCHEMA)
        for relation in self.model.relations[self._relation_name]:
            relation.data[self.model.app].update(rel_data)

    @property
    def requirer_data(self) -> Mapping[str, str]:
        relation = self.model.get_relation(relation_name=self._relation_name)
        if not relation or not relation.app:
            return {}

        rel_data = relation.data[relation.app]
        if not rel_data:
            return {}

        try:
            data = _load_data(
                relation.data[relation.app],
                REQUIRER_JSON_SCHEMA,
            )
        except DataValidationError as e:
            logger.info(f"failed to validate relation data: {e}")
            return {}

        return data


class KeystoneSAMLRequirerChangedEvent(EventBase):
    """Event to notify the charm that the information in the databag changed."""

    def __init__(
        self, handle: Handle, metadata: str, name: str, label: str, ca_chain: str
    ):
        super().__init__(handle)
        self.metadata = metadata
        self.name = name
        self.label = label
        self.ca_chain = ca_chain

    def snapshot(self) -> Dict:
        """Save event."""
        return {
            "metadata": self.metadata,
            "name": self.name,
            "label": self.label,
            "ca_chain": self.ca_chain,
        }

    def restore(self, snapshot: Dict) -> None:
        """Restore event."""
        super().restore(snapshot)
        self.metadata = snapshot["metadata"]
        self.name = snapshot["name"]
        self.label = snapshot["label"]
        self.ca_chain = snapshot["ca_chain"]


class KeystoneSAMLRequirerEvents(ObjectEvents):
    """Event descriptor for events raised by `KeystoneSAMLRequirerEvents`."""

    changed = EventSource(KeystoneSAMLRequirerChangedEvent)


class KeystoneSAMLRequirer(Object):

    on = KeystoneSAMLRequirerEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        events = self._charm.on[relation_name]
        self.framework.observe(
            events.relation_changed,
            self._on_relation_changed_event)
        self.framework.observe(
            events.relation_broken,
            self._on_relation_broken_event)

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Handle relation changed event."""
        try:
            data = _load_data(
                event.relation.data[event.relation.app],
                PROVIDER_JSON_SCHEMA,
            )
        except DataValidationError as e:
            logger.error(f"failed to validate relation data: {e}")
            return
        if not data:
            logger.info("No requirer relation data available.")
            return


        self.on.changed.emit(
            metadata=data["metadata"],
            name=data["name"],
            label=data["label"],
            ca_chain=data.get("ca_chain", []),
        )


    def _on_relation_broken_event(self, event: RelationBrokenEvent) -> None:
        """Handle relation broken event."""

        logger.info("Relation broken, clearing data.")
        self.on.changed.emit(
            metadata="",
            name="",
            label="",
            ca_chain=[],
        )

    @property
    def relations(self) -> list[Relation]:
        return [
            relation
            for relation in self._charm.model.relations[self._relation_name]
            if relation.active
        ]

    def set_requirer_info(
        self, info: Mapping[str, str], relation_id: int
    ) -> None:
        if not self.model.unit.is_leader():
            return

        relation = self.model.get_relation(
            relation_name=self._relation_name, relation_id=relation_id
        )
        if not relation:
            return

        rel_data = _dump_data(info, REQUIRER_JSON_SCHEMA)
        relation.data[self.model.app].update(rel_data)

    def get_providers(self) -> List[Mapping[str, str]]:
        providers = []
        names = []

        for relation in self.relations:
            if not relation or not relation.app:
                continue

            rel_data = relation.data[relation.app]
            if not rel_data:
                continue

            try:
                data = _load_data(
                    relation.data[relation.app],
                    PROVIDER_JSON_SCHEMA,
                )
            except DataValidationError as e:
                logger.error(f"failed to validate relation data: {e}")
                continue
        
            try:
                decoded = base64.b64decode(data["metadata"]).decode()
                data["metadata"] = decoded
            except Exception as e:
                logger.error(f"failed to decode metadata: {e}")
                continue
            if data["name"] in names:
                raise ValueError(
                    f"duplicate provider name in relation data: {data['name']}"
                )
            names.append(data["name"])
            data["relation_id"] = relation.id
            providers.append(data)
        return providers
