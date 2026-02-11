"""IdentityResourceProvides and Requires module.

This library contains the Requires and Provides classes for handling
the identity_ops interface.

Import `IdentityResourceRequires` in your charm, with the charm object and the
relation name:
    - self
    - "identity_ops"

Also provide additional parameters to the charm object:
    - request

Three events are also available to respond to:
    - provider_ready
    - provider_goneaway
    - response_avaialable

A basic example showing the usage of this relation follows:

```
from charms.keystone_k8s.v0.identity_resource import IdentityResourceRequires

class IdentityResourceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # IdentityResource Requires
        self.identity_resource = IdentityResourceRequires(
            self, "identity_ops",
        )
        self.framework.observe(
            self.identity_resource.on.provider_ready, self._on_identity_resource_ready)
        self.framework.observe(
            self.identity_resource.on.provider_goneaway, self._on_identity_resource_goneaway)
        self.framework.observe(
            self.identity_resource.on.response_available, self._on_identity_resource_response)

    def _on_identity_resource_ready(self, event):
        '''React to the IdentityResource provider_ready event.

        This event happens when n IdentityResource relation is added to the
        model. Ready to send any ops to keystone.
        '''
        # Ready to send any ops.
        pass

    def _on_identity_resource_response(self, event):
        '''React to the IdentityResource response_available event.

        The IdentityResource interface will provide the response for the ops sent.
        '''
        # Read the response for the ops sent.
        pass

    def _on_identity_resource_goneaway(self, event):
        '''React to the IdentityResource goneaway event.

        This event happens when an IdentityResource relation is removed.
        '''
        # IdentityResource Relation has goneaway. No ops can be sent.
        pass
```

A sample ops request can be of format
{
    "id": <request id>
    "tag": <string to identify request>
    "ops": [
        {
            "name": <op name>,
            "params": {
                <param 1>: <value 1>,
                <param 2>: <value 2>
            }
        }
    ]
}

For any sensitive data in the ops params, the charm can create secrets and pass
secret id instead of sensitive data as part of ops request. The charm should
ensure to grant secret access to provider charm i.e., keystone over relation.
The secret content should hold the sensitive data with same name as param name.
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
    RelationJoinedEvent,
)
from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)
from ops.model import (
    Relation,
)

logger = logging.getLogger(__name__)


# The unique Charmhub library identifier, never change it
LIBID = "b419d4d8249e423487daafc3665ed06f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 5


REQUEST_NOT_SENT = 1
REQUEST_SENT = 2
REQUEST_PROCESSED = 3

STATUS_READY = "ready"
STATUS_NOT_READY = "not_ready"

class IdentityOpsProviderReadyEvent(RelationEvent):
    """Has IdentityOpsProviderReady Event."""

    pass


class IdentityOpsResponseEvent(RelationEvent):
    """Has IdentityOpsResponse Event."""

    pass


class IdentityOpsProviderGoneAwayEvent(RelationEvent):
    """Has IdentityOpsProviderGoneAway Event."""

    pass


class IdentityResourceResponseEvents(ObjectEvents):
    """Events class for `on`."""

    provider_ready = EventSource(IdentityOpsProviderReadyEvent)
    response_available = EventSource(IdentityOpsResponseEvent)
    provider_goneaway = EventSource(IdentityOpsProviderGoneAwayEvent)


class IdentityResourceRequires(Object):
    """IdentityResourceRequires class."""

    on = IdentityResourceResponseEvents()
    _stored = StoredState()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self._stored.set_default(provider_gone=False, requests=[])
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_identity_resource_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_resource_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_resource_relation_broken,
        )

    def _on_identity_resource_relation_joined(
        self, event: RelationJoinedEvent
    ):
        """Handle IdentityResource joined."""
        self.on.provider_ready.emit(event.relation)

    def _on_identity_resource_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle IdentityResource changed."""
        id_ = self.response.get("id")
        self.save_request_in_store(id_, None, None, REQUEST_PROCESSED)
        self.on.response_available.emit(event.relation)

    def _on_identity_resource_relation_broken(
        self, event: RelationBrokenEvent
    ):
        """Handle IdentityResource broken."""
        self._stored.provider_gone = True
        self.on.provider_goneaway.emit(event.relation)

    @property
    def _identity_resource_rel(self) -> Optional[Relation]:
        """The IdentityResource relation."""
        return self.framework.model.get_relation(self.relation_name)

    @property
    def response(self) -> dict:
        """Response object from keystone."""
        response = self.get_remote_app_data("response")
        if not response:
            return {}

        try:
            return json.loads(response)
        except Exception as e:
            logger.debug(str(e))

        return {}

    def save_request_in_store(
        self, id: str, tag: str, ops: list, state: int
    ) -> None:
        """Save request in the store."""
        if id is None:
            return

        for request in self._stored.requests:
            if request.get("id") == id:
                if tag:
                    request["tag"] = tag
                if ops:
                    request["ops"] = ops
                request["state"] = state
                return

        # New request
        self._stored.requests.append(
            {"id": id, "tag": tag, "ops": ops, "state": state}
        )

    def get_request_from_store(self, id: str) -> dict:
        """Get request from the stote."""
        for request in self._stored.requests:
            if request.get("id") == id:
                return request

        return {}

    @property
    def provider_ready(self) -> bool:
        """Check if provider is ready.

        Exit hatch not to check the remote app data if
        we know the provider is gone.
        """
        return not self._stored.provider_gone and self.get_remote_app_data("status") == STATUS_READY

    def is_request_processed(self, id: str) -> bool:
        """Check if request is processed."""
        for request in self._stored.requests:
            if (
                request.get("id") == id
                and request.get("state") == REQUEST_PROCESSED
            ):
                return True

        return False

    def get_remote_app_data(self, key: str) -> Optional[str]:
        """Return the value for the given key from remote app data."""
        if self._identity_resource_rel:
            data = self._identity_resource_rel.data[
                self._identity_resource_rel.app
            ]
            return data.get(key)

        return None

    def ready(self) -> bool:
        """Interface is ready or not.

        Interface is considered ready if the op request is processed
        and response is sent. In case of non leader unit, just consider
        the interface is ready.
        """
        if not self.model.unit.is_leader():
            logger.debug("Not a leader unit, set the interface to ready")
            return True

        try:
            app_data = self._identity_resource_rel.data[self.charm.app]
            if "request" not in app_data:
                return False

            request = json.loads(app_data["request"])
            request_id = request.get("id")
            response_id = self.response.get("id")
            if request_id == response_id:
                return True
        except Exception as e:
            logger.debug(str(e))

        return False

    def request_ops(self, request: dict) -> None:
        """Request keystone ops."""
        if not self.model.unit.is_leader():
            logger.debug("Not a leader unit, not sending request")
            return

        id_ = request.get("id")
        tag = request.get("tag")
        ops = request.get("ops")
        req = self.get_request_from_store(id_)
        if req and req.get("state") == REQUEST_PROCESSED:
            logger.debug("Request %s already processed", id_)
            return

        if not self.provider_ready:
            self.save_request_in_store(id_, tag, ops, REQUEST_NOT_SENT)
            logger.debug("Keystone not yet ready to take requests")
            return

        logger.debug("Requesting ops to keystone")
        app_data = self._identity_resource_rel.data[self.charm.app]
        app_data["request"] = json.dumps(request)
        self.save_request_in_store(id_, tag, ops, REQUEST_SENT)


class IdentityOpsRequestEvent(EventBase):
    """Has IdentityOpsRequest Event."""

    def __init__(self, handle, relation_id, relation_name, request):
        """Initialise event."""
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name
        self.request = request

    def snapshot(self):
        """Snapshot the event."""
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
            "request": self.request,
        }

    def restore(self, snapshot):
        """Restore the event."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]
        self.request = snapshot["request"]


class IdentityOpsRequesterGoneAwayEvent(EventBase):
    """Has IdentityOpsRequesterGoneAway Event."""

    def __init__(self, handle, relation_id, relation_name):
        """Initialise event."""
        super().__init__(handle)
        self.relation_id = relation_id
        self.relation_name = relation_name

    def snapshot(self):
        """Snapshot the event."""
        return {
            "relation_id": self.relation_id,
            "relation_name": self.relation_name,
        }

    def restore(self, snapshot):
        """Restore the event."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]


class IdentityResourceProviderEvents(ObjectEvents):
    """Events class for `on`."""

    process_op = EventSource(IdentityOpsRequestEvent)
    goneaway = EventSource(IdentityOpsRequesterGoneAwayEvent)


class IdentityResourceProvides(Object):
    """IdentityResourceProvides class."""

    on = IdentityResourceProviderEvents()

    def __init__(self, charm: CharmBase, relation_name: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_resource_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_resource_relation_broken,
        )

    def _on_identity_resource_relation_changed(
        self, event: RelationChangedEvent
    ):
        """Handle IdentityResource changed."""
        request = event.relation.data[event.relation.app].get("request")
        if request is not None:
            self.on.process_op.emit(
                event.relation.id, event.relation.name, request
            )

    def _on_identity_resource_relation_broken(
        self, event: RelationBrokenEvent
    ):
        """Handle IdentityResource broken."""
        self.on.goneaway.emit(event.relation.id, event.relation.name)

    def publish_status(self, status: str):
        """Publish status to the related app."""
        if status not in [STATUS_READY, STATUS_NOT_READY]:
            logger.debug(f"Invalid status {status}, not publishing")
            return
        if not self.model.unit.is_leader():
            logger.debug("Not a leader unit, not sending status")
            return

        logger.debug("Publishing status to related app")
        for relation in self.model.relations[self.relation_name]:
            app_data = relation.data[relation.app]
            app_data["status"] = status

    def set_ops_response(
        self, relation_id: str, relation_name: str, ops_response: dict
    ) -> None:
        """Set response to ops request."""
        if not self.model.unit.is_leader():
            logger.debug("Not a leader unit, not sending response")
            return

        logger.debug("Update response from keystone")
        _identity_resource_rel = self.charm.model.get_relation(
            relation_name, relation_id
        )
        if not _identity_resource_rel:
            # Relation has disappeared so skip send of data
            return

        app_data = _identity_resource_rel.data[self.charm.app]
        app_data["response"] = json.dumps(ops_response)
