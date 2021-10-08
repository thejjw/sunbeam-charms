"""IdentityServiceProvides and Requires module.


This library contains the Requires and Provides classes for handling
the identity_service interface.

Import `IdentityServiceRequires` in your charm, with the charm object and the
relation name:
    - self
    - "identity_service"

Also provide additional parameters to the charm object:
    - service
    - internal_url
    - public_url
    - admin_url
    - region
    - username
    - vhost

Two events are also available to respond to:
    - connected
    - ready
    - goneaway

A basic example showing the usage of this relation follows:

```
from charms.sunbeam_sunbeam_identity_service_operator.v0.identity_service import IdentityServiceRequires

class IdentityServiceClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # IdentityService Requires
        self.identity_service = IdentityServiceRequires(
            self, "identity_service",
            service = "my-service"
            internal_url = "http://internal-url"
            public_url = "http://public-url"
            admin_url = "http://admin-url"
            region = "region"
        )
        self.framework.observe(
            self.identity_service.on.connected, self._on_identity_service_connected)
        self.framework.observe(
            self.identity_service.on.ready, self._on_identity_service_ready)
        self.framework.observe(
            self.identity_service.on.goneaway, self._on_identity_service_goneaway)

    def _on_identity_service_connected(self, event):
        '''React to the IdentityService connected event.

        This event happens when n IdentityService relation is added to the
        model before credentials etc have been provided.
        '''
        # Do something before the relation is complete
        pass

    def _on_identity_service_ready(self, event):
        '''React to the IdentityService ready event.

        The IdentityService interface will use the provided config for the
        request to the identity server.
        '''
        # IdentityService Relation is ready. Do something with the completed relation.
        pass

    def _on_identity_service_goneaway(self, event):
        '''React to the IdentityService goneaway event.

        This event happens when an IdentityService relation is removed.
        '''
        # IdentityService Relation has goneaway. shutdown services or suchlike
        pass
```
"""

# The unique Charmhub library identifier, never change it
# LIBID = ""

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

import logging
import requests

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object,
)

from ops.model import Relation

from typing import List

logger = logging.getLogger(__name__)


class IdentityServiceConnectedEvent(EventBase):
    """IdentityService connected Event."""

    pass


class IdentityServiceReadyEvent(EventBase):
    """IdentityService ready for use Event."""

    pass


class IdentityServiceGoneAwayEvent(EventBase):
    """IdentityService relation has gone-away Event"""

    pass


class IdentityServiceServerEvents(ObjectEvents):
    """Events class for `on`"""

    connected = EventSource(IdentityServiceConnectedEvent)
    ready = EventSource(IdentityServiceReadyEvent)
    goneaway = EventSource(IdentityServiceGoneAwayEvent)


class IdentityServiceRequires(Object):
    """
    IdentityServiceRequires class
    """

    on = IdentityServiceServerEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name: str, service: str,
                 internal_url: str, public_url: str, admin_url: str,
                 region: str):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.service = service
        self.internal_url = internal_url
        self.public_url = public_url
        self.admin_url = admin_url
        self.region = region
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_identity_service_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_service_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_departed,
            self._on_identity_service_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_service_relation_broken,
        )

    def _on_identity_service_relation_joined(self, event):
        """IdentityService relation joined."""
        logging.debug("IdentityService on_joined")
        self.on.connected.emit()
        self.register_service(
            self.service,
            self.internal_url,
            self.public_url,
            self.admin_url,
            self.region)

    def _on_identity_service_relation_changed(self, event):
        """IdentityService relation changed."""
        logging.debug("IdentityService on_changed")
        if self.password:
            self.on.ready.emit()

    def _on_identity_service_relation_broken(self, event):
        """IdentityService relation broken."""
        logging.debug("IdentityService on_broken")
        self.on.goneaway.emit()

    @property
    def _identity_service_rel(self) -> Relation:
        """The IdentityService relation."""
        return self.framework.model.get_relation(self.relation_name)

    def get_remote_app_data(self, key: str) -> str:
        """Return the value for the given key from remote app data."""
        data = self._identity_service_rel.data[self._identity_service_rel.app]
        return data.get(key)

    @property
    def api_version(self) -> str:
        """Return the admin_domain_id."""
        return self.get_remote_app_data('api-version')

    @property
    def auth_host(self) -> str:
        """Return the auth_host."""
        return self.get_remote_app_data('auth-host')

    @property
    def auth_port(self) -> str:
        """Return the auth_port."""
        return self.get_remote_app_data('auth-port')

    @property
    def auth_protocol(self) -> str:
        """Return the auth_protocol."""
        return self.get_remote_app_data('auth-protocol')

    @property
    def internal_host(self) -> str:
        """Return the internal_host."""
        return self.get_remote_app_data('internal-host')

    @property
    def internal_port(self) -> str:
        """Return the internal_port."""
        return self.get_remote_app_data('internal-port')

    @property
    def internal_protocol(self) -> str:
        """Return the internal_protocol."""
        return self.get_remote_app_data('internal-protocol')

    @property
    def service_domain(self) -> str:
        """Return the internal_port."""
        return self.get_remote_app_data('service-domain')

    @property
    def service_domain_id(self) -> str:
        """Return the internal_port."""
        return self.get_remote_app_data('service-domain-id')

    @property
    def service_host(self) -> str:
        """Return the service_host."""
        return self.get_remote_app_data('service-host')

    @property
    def service_password(self) -> str:
        """Return the service_password."""
        return self.get_remote_app_data('service-password')

    @property
    def service_port(self) -> str:
        """Return the service_port."""
        return self.get_remote_app_data('service-port')

    @property
    def service_protocol(self) -> str:
        """Return the service_protocol."""
        return self.get_remote_app_data('service-protocol')

    @property
    def service_project(self) -> str:
        """Return the service_project."""
        return self.get_remote_app_data('service-project')

    @property
    def service_project_id(self) -> str:
        """Return the service_project."""
        return self.get_remote_app_data('service-project-id')

    def register_service(self, service: str, internal_url: str,
                         public_url: str, admin_url: str, region: str) -> None:
        """Request access to the IdentityService server."""
        if self.model.unit.is_leader():
            logging.debug("Requesting service registration")
            app_data = self._identity_service_rel.data[self.charm.app]
            app_data["service"] = service
            app_data["internal-url"] = internal_url
            app_data["public-url"] = public_url
            app_data["admin-url"] = admin_url
            app_data["region"] = region


class HasIdentityServiceClientsEvent(EventBase):
    """Has IdentityServiceClients Event."""

    pass


class ReadyIdentityServiceClientsEvent(EventBase):
    """IdentityServiceClients Ready Event."""

    pass


class IdentityServiceClientEvents(ObjectEvents):
    """Events class for `on`"""

    has_identity_service_clients = EventSource(HasIdentityServiceClientsEvent)
    ready_identity_service_clients = EventSource(ReadyIdentityServiceClientsEvent)


class IdentityServiceProvides(Object):
    """
    IdentityServiceProvides class
    """

    on = IdentityServiceClientEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._on_identity_service_relation_joined,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_identity_service_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_identity_service_relation_broken,
        )

    def _on_identity_service_relation_joined(self, event):
        """Handle IdentityService joined."""
        logging.debug("IdentityService on_joined")
        self.on.has_identity_service_clients.emit()

    def _on_identity_service_relation_changed(self, event):
        """Handle IdentityService changed."""
        logging.debug("IdentityService on_changed")
        REQUIRED_KEYS = [
            'service',
            'internal-url',
            'public-url',
            'admin-url',
            'region']
        
        values = [
            event.relation.data[event.relation.app].get(k)
            for k in REQUIRED_KEYS ]
        # Validate data on the relation
        if all(values):
            self.on.ready_identity_service_clients.emit()

    def _on_identity_service_relation_broken(self, event):
        """Handle IdentityService broken."""
        logging.debug("IdentityServiceProvides on_departed")
        # TODO clear data on the relation

    def set_identity_service_credentials(self, admin_domain_id: str,
                                         admin_project_id: str,
                                         admin_user_id: str,
                                         api_version: str,
                                         auth_host: str, auth_port: str,
                                         auth_protocol: str,
                                         internal_host: str,
                                         internal_port: str,
                                         internal_protocol: str,
                                         service_domain: str,
                                         service_domain_id: str,
                                         service_host: str,
                                         service_password: str,
                                         service_port: str,
                                         service_protocol: str,
                                         service_tenant: str,
                                         service_tenant_id: str,
                                         service_username: str):
        logging.debug("Setting identity_service connection information.")
        app_data = self._identity_service_rel.data[self.charm.app]
        app_data["admin-domain-id"] = admin_domain_id
        app_data["admin-project-id"] = admin_project_id
        app_data["admin-user-id"] = admin_user_id
        app_data["api-version"] = api_version
        app_data["auth-host"] = auth_host
        app_data["auth-port"] = auth_port
        app_data["auth-protocol"] = auth_protocol
        app_data["internal-host"] = internal_host
        app_data["internal-port"] = internal_port
        app_data["internal-protocol"] = internal_protocol
        app_data["service-domain"] = service_domain
        app_data["service-domain_id"] = service_domain_id
        app_data["service-host"] = service_host
        app_data["service-password"] = service_password
        app_data["service-port"] = service_port
        app_data["service-protocol"] = service_protocol
        app_data["service-project"] = service_project
        app_data["service-project-id"] = service_project_id
        app_data["service-username"] = service_username
