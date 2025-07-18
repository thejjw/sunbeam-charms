# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import jsonschema
import logging

from ops.charm import CharmBase, RelationBrokenEvent, RelationChangedEvent
from ops.framework import EventBase, EventSource, Handle, Object, ObjectEvents
from ops.model import TooManyRelatedAppsError
from typing import Dict, List, Mapping, Optional


logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "61f4f7e4e91947c9bbb12f4e262ee244"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

PYDEPS = ["jsonschema"]

DEFAULT_RELATION_NAME = "trusted-dashboard"

TRUSTED_DASHBOARD_PROVIDER_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "dashboard-url": {
            "type": "string",
            "default": None,
            "description": "The URL of the trusted dashboard.",
            "format": "uri",
            "pattern": "^https?://",
            "examples": [
                "https://dashboard.example.com",
                "http://horizon.example.com:8080/websso"
            ]
        }
    },
    "required": ["dashboard-url"]
}

TRUSTED_DASHBOARD_REQUIRE_JSON_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema",
    "type": "object",
    "properties": {
        "federated-providers": {
            "type": "array",
            "default": [],
            "items": {
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "enum": ["openid", "saml2"],
                        "default": None
                    },
                    "name": {
                        "type": "string",
                        "default": None
                    },
                    "description": {
                        "type": "string",
                        "default": None
                    }
                },
                "required": ["protocol", "name"]
            },
            "description": "List of federated providers that the dashboard should enable.",
            "examples": [
                {
                    "protocol": "openid",
                    "name": "example-openid-provider",
                    "description": "An OpenID Connect provider for federated authentication."
                },
                {
                    "protocol": "saml2",
                    "name": "example-saml-provider",
                    "description": "A SAML provider for federated authentication."
                }
            ]
        }
    },
    "required": ["federated-providers"]
}

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


class TrustedDashboadProvidersChangedEvent(EventBase):
    """Event to notify the charm that the information in the databag changed."""

    def __init__(self, handle: Handle, fid_providers: list):
        super().__init__(handle)
        self.fid_providers = fid_providers

    def snapshot(self) -> Dict:
        """Save event."""
        return {
            "fid_providers": self.fid_providers,
        }

    def restore(self, snapshot: Dict) -> None:
        """Restore event."""
        super().restore(snapshot)
        self.fid_providers = snapshot["fid_providers"]


class TrustedDashboadChangedEvent(EventBase):
    """Event to notify the charm that the information in the databag changed."""

    def __init__(self, handle: Handle, trusted_dashboard: str):
        super().__init__(handle)
        self.trusted_dashboard = trusted_dashboard

    def snapshot(self) -> Dict:
        """Save event."""
        return {
            "trusted_dashboard": self.trusted_dashboard,
        }

    def restore(self, snapshot: Dict) -> None:
        """Restore event."""
        super().restore(snapshot)
        self.trusted_dashboard = snapshot["trusted_dashboard"]


class TrustedDashboardRequirerEvents(ObjectEvents):
    """Event descriptor for events raised by `TrustedDashboardRequirerEvents`."""

    dashboard_changed = EventSource(TrustedDashboadChangedEvent)


class TrustedDashboardProviderEvents(ObjectEvents):
    """Event descriptor for events raised by `TrustedDashboardProviderEvents`."""

    providers_changed = EventSource(TrustedDashboadProvidersChangedEvent)


class TrustedDashboardProvider(Object):

    on = TrustedDashboardProviderEvents()

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

        provider_data = {
            "federated-providers": data.get("federated-providers", [])
        }

        if not provider_data["federated-providers"]:
            logger.info("No federated providers found in relation data.")
            return

        data = _load_data(provider_data, TRUSTED_DASHBOARD_REQUIRE_JSON_SCHEMA)
        self.on.providers_changed.emit(
            fid_providers=provider_data["federated-providers"]
        )

    @property
    def fid_providers(self) -> List[Mapping[str, str]]:
        # This relation is limited to 1.
        relation = self.model.get_relation(relation_name=self._relation_name)
        if not relation:
            return []

        rel_data = relation.data[relation.app]
        if not rel_data:
            return []
        data = _load_data(
            relation.data[relation.app],
            TRUSTED_DASHBOARD_REQUIRE_JSON_SCHEMA)

        return data.get("federated-providers", None)

    def _on_relation_broken_event(self, event: RelationBrokenEvent) -> None:
        """Handle relation broken event."""
        logger.info("Relation broken, clearing federated providers.")
        self.on.providers_changed.emit(fid_providers=[])

    def set_provider_info(self, trusted_dashboard: str) -> None:
        if not self.model.unit.is_leader():
            return

        data = {
            "dashboard-url": trusted_dashboard
        }

        _validate_data(data, TRUSTED_DASHBOARD_PROVIDER_JSON_SCHEMA)

        for relation in self.model.relations[self._relation_name]:
            relation.data[self.model.app].update(data)


class TrustedDashboardRequirer(Object):

    on = TrustedDashboardRequirerEvents()

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

        requirer_data = {
            "dashboard-url": data.get("dashboard-url", "")
        }

        if not requirer_data["dashboard-url"]:
            logger.info("No trustwed dashboard found in relation data.")
            return

        _validate_data(requirer_data, TRUSTED_DASHBOARD_PROVIDER_JSON_SCHEMA)
        self.on.dashboard_changed.emit(
            trusted_dashboard=requirer_data["dashboard-url"]
        )

    def _on_relation_broken_event(self, event: RelationBrokenEvent) -> None:
        """Handle relation broken event."""

        logger.info("Relation broken, clearing federated providers.")
        self.on.dashboard_changed.emit(trusted_dashboard="")

    def get_trusted_dashboard(self, relation_id: Optional[int] = None) -> Optional[str]:
        """Get the trusted dashboard URL from the relation data."""
        try:
            relation = self.model.get_relation(
                relation_name=self._relation_name, relation_id=relation_id
            )
        except TooManyRelatedAppsError:
            raise RuntimeError("More than one relations are defined. Please provide a relation_id")

        if not relation or not relation.app:
            return None

        data = relation.data[relation.app]
        return data.get("dashboard-url", None)

    def set_requirer_info(self, federated_providers: Dict, relation_id: Optional[int] = None) -> None:
        if not self.model.unit.is_leader():
            return

        try:
            relation = self.model.get_relation(
                relation_name=self._relation_name, relation_id=relation_id
            )
        except TooManyRelatedAppsError:
            raise RuntimeError("More than one relations are defined. Please provide a relation_id")

        if not relation or not relation.app:
            return

        if not federated_providers:
            federated_providers = {
                    "federated-providers": []
            }
        relation.data[self.model.app].update(
            _dump_data(
                federated_providers,
                TRUSTED_DASHBOARD_REQUIRE_JSON_SCHEMA))
