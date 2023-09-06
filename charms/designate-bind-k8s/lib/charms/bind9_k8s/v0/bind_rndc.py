# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
"""TODO: Add a proper docstring here.

This is a placeholder docstring for this charm library. Docstrings are
presented on Charmhub and updated whenever you push a new version of the
library.

Complete documentation about creating and documenting libraries can be found
in the SDK docs at https://juju.is/docs/sdk/libraries.

See `charmcraft publish-lib` and `charmcraft fetch-lib` for details of how to
share and consume charm libraries. They serve to enhance collaboration
between charmers. Use a charmer's libraries for classes that handle
integration with their charm.

Bear in mind that new revisions of the different major API versions (v0, v1,
v2 etc) are maintained independently.  You can continue to update v0 and v1
after you have pushed v3.

Markdown is supported, following the CommonMark specification.
"""

import json
import logging
import secrets
from typing import (
    Any,
)

import ops

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "0fb2f64f2a1344feb80044cee22ef3a8"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class BindRndcReadyEvent(ops.EventBase):
    """Bind rndc ready event."""

    def __init__(
        self,
        handle: ops.Handle,
        relation_id: int,
        relation_name: str,
        algorithm: str,
        secret: str,
    ):
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name
        self.algorithm = algorithm
        self.secret = secret

    def snapshot(self) -> dict:
        """Return snapshot data that should be persisted."""
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
            "algorithm": self.algorithm,
            "secret": self.secret,
        }

    def restore(self, snapshot: dict[str, Any]):
        """Restore the value state from a given snapshot."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]
        self.algorithm = snapshot["algorithm"]
        self.secret = snapshot["secret"]


class BindRndcRequirerEvents(ops.ObjectEvents):
    """List of events that the BindRndc requires charm can leverage."""

    bind_rndc_ready = ops.EventSource(BindRndcReadyEvent)


class BindRndcRequires(ops.Object):
    """Class to be instantiated by the requiring side of the relation."""

    on = BindRndcRequirerEvents()
    _stored = ops.StoredState()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self._stored.set_default(nonce="")
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_joined(self, event: ops.RelationJoinedEvent):
        """Handle relation joined event."""
        self._request_rndc_key(event.relation)

    def _on_relation_changed(self, event: ops.RelationJoinedEvent):
        """Handle relation changed event."""
        host = self.host(event.relation)
        rndc_key = self.get_rndc_key(event.relation)
        if rndc_key is None:
            self._request_rndc_key(event.relation)
            return

        if host is not None:
            algorithm = rndc_key["algorithm"]
            secret = rndc_key["secret"]
            self.on.bind_rndc_ready.emit(
                event.relation.id,
                event.relation.name,
                algorithm,
                secret,
            )

    def host(self, relation: ops.Relation) -> str | None:
        """Return host from relation."""
        if relation.app is None:
            return None
        return relation.data[relation.app].get("host")

    def nonce(self) -> str:
        """Return nonce from stored state."""
        return self._stored.nonce

    def get_rndc_key(self, relation: ops.Relation) -> dict | None:
        """Get rndc keys."""
        if relation.app is None:
            return None
        if self._stored.nonce == "":
            logger.debug("No nonce set for unit yet")
            return None

        return json.loads(
            relation.data[relation.app].get("rndc_keys", "{}")
        ).get(self._stored.nonce)

    def _request_rndc_key(self, relation: ops.Relation):
        """Request rndc key over the relation."""
        if self._stored.nonce == "":
            self._stored.nonce = secrets.token_hex(16)
            relation.data[self.charm.unit]["nonce"] = self._stored.nonce

    def reconcile_rndc_key(self, relation: ops.Relation):
        """Reconcile rndc key over the relation."""
        if self._stored.nonce != relation.data[self.charm.unit].get("nonce"):
            self._stored.nonce = secrets.token_hex(16)
            relation.data[self.charm.unit]["nonce"] = self._stored.nonce


class NewBindClientAttachedEvent(ops.EventBase):
    """New bind client attached event."""

    def __init__(
        self,
        handle: ops.Handle,
        relation_id: int,
        relation_name: str,
    ):
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name

    def snapshot(self) -> dict:
        """Return snapshot data that should be persisted."""
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
        }

    def restore(self, snapshot: dict[str, Any]):
        """Restore the value state from a given snapshot."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]


class BindClientUpdatedEvent(ops.EventBase):
    """Bind client updated event."""

    def __init__(
        self,
        handle: ops.Handle,
        relation_id: int,
        relation_name: str,
    ):
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name

    def snapshot(self) -> dict:
        """Return snapshot data that should be persisted."""
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
        }

    def restore(self, snapshot: dict[str, Any]):
        """Restore the value state from a given snapshot."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]


class BindRndcProviderEvents(ops.ObjectEvents):
    """List of events that the BindRndc provider charm can leverage."""

    new_bind_client_attached = ops.EventSource(NewBindClientAttachedEvent)
    bind_client_updated = ops.EventSource(BindClientUpdatedEvent)


class BindRndcProvides(ops.Object):
    """Class to be instantiated by the providing side of the relation."""

    on = BindRndcProviderEvents()

    def __init__(self, charm: ops.CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )

    def _on_relation_joined(self, event: ops.RelationJoinedEvent):
        self.on.new_bind_client_attached.emit(
            event.relation.id, event.relation.name
        )
        if not self.charm.unit.is_leader():
            return
        binding = self.model.get_binding(event.relation)
        if binding is None:
            raise Exception("No binding found")
        address = binding.network.ingress_address
        event.relation.data[self.charm.app]["host"] = str(address)

    def _on_relation_changed(self, event: ops.RelationChangedEvent):
        self.on.bind_client_updated.emit(
            event.relation.id, event.relation.name
        )

    def get_rndc_keys(self, relation: ops.Relation) -> dict:
        """Get rndc keys."""
        return json.loads(relation.data[self.charm.app].get("rndc_keys", "{}"))

    def set_rndc_client_key(
        self,
        relation: ops.Relation,
        client: str,
        algorithm: str,
        secret: ops.Secret,
    ):
        """Add rndc key to the relation.

        `rndc_keys` is a dict of dicts, keyed by client name. Each client
        has an algorithm and secret property. The secret is a Juju secret id,
        containing the actual secret needed to communicate over rndc.
        """
        if not self.charm.unit.is_leader():
            logger.debug("Not leader, skipping set_rndc_client_key")
            return

        keys = self.get_rndc_keys(relation)
        keys[client] = {
            "algorithm": algorithm,
            "secret": secret.id,
        }

        relation.data[self.charm.app]["rndc_keys"] = json.dumps(
            keys, sort_keys=True
        )

    def remove_rndc_client_key(
        self,
        relation: ops.Relation,
        client: str | list[str],
    ):
        """Remove rndc key from the relation."""
        if not self.charm.unit.is_leader():
            logger.debug("Not leader, skipping remove_rndc_client_key")
            return
        if isinstance(client, str):
            client = [client]
        keys = self.get_rndc_keys(relation)
        for c in client:
            keys.pop(c)
        relation.data[self.charm.app]["rndc_keys"] = json.dumps(
            keys, sort_keys=True
        )
