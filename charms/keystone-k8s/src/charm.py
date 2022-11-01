#!/usr/bin/env python3
#
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
#
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import json
import logging
import os
import pwgen
import subprocess
import time
from typing import Callable, List, Dict, Optional

import ops.charm
from ops.charm import (
    CharmEvents,
    RelationChangedEvent,
    RelationEvent,
    HookEvent,
    ActionEvent,
)
import ops.pebble
from ops.main import main
from ops.framework import StoredState, Object, EventSource
from ops import model

from utils import manager
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.config_contexts as sunbeam_contexts
import ops_sunbeam.relation_handlers as sunbeam_rhandlers

from ops_sunbeam.interfaces import OperatorPeers

import charms.keystone_k8s.v0.identity_service as sunbeam_id_svc
import charms.keystone_k8s.v0.cloud_credentials as sunbeam_cc_svc

logger = logging.getLogger(__name__)

KEYSTONE_CONTAINER = "keystone"
LAST_FERNET_KEY_ROTATION_KEY = "last_fernet_rotation"
FERNET_KEYS_KEY = "fernet_keys"


KEYSTONE_CONF = '/etc/keystone/keystone.conf'
LOGGING_CONF = '/etc/keystone/logging.conf'


class KeystoneLoggingAdapter(sunbeam_contexts.ConfigContext):

    def context(self):
        config = self.charm.model.config
        ctxt = {}
        if config['debug']:
            ctxt['root_level'] = 'DEBUG'
        log_level = config['log-level']
        if log_level in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            ctxt['log_level'] = log_level
        else:
            logger.error('log-level must be one of the following values '
                         f'(DEBUG, INFO, WARNING, ERROR) not "{log_level}"')
            ctxt['log_level'] = None
        ctxt['log_file'] = '/var/log/keystone/keystone.log'
        return ctxt


class KeystoneConfigAdapter(sunbeam_contexts.ConfigContext):

    def context(self):
        config = self.charm.model.config
        return {
            'api_version': 3,
            'admin_role': self.charm.admin_role,
            'assignment_backend': 'sql',
            'service_tenant_id': self.charm.service_project_id,
            'admin_domain_name': self.charm.admin_domain_name,
            'admin_domain_id': self.charm.admin_domain_id,
            'auth_methods': 'external,password,token,oauth1,mapped',
            'default_domain_id': self.charm.default_domain_id,
            'public_port': self.charm.service_port,
            'debug': config['debug'],
            'token_expiration': config['token-expiration'],
            'allow_expired_window': config['allow-expired-window'],
            'catalog_cache_expiration': config['catalog-cache-expiration'],
            'dogpile_cache_expiration': config['dogpile-cache-expiration'],
            'identity_backend': 'sql',
            'token_provider': 'fernet',
            'fernet_max_active_keys': config['fernet-max-active-keys'],
            'public_endpoint': self.charm.public_endpoint,
            'admin_endpoint': self.charm.admin_endpoint,
            'domain_config_dir': '/etc/keystone/domains',
            'log_config': '/etc/keystone/logging.conf.j2',
            'paste_config_file': '/etc/keystone/keystone-paste.ini',
        }


class IdentityServiceProvidesHandler(sunbeam_rhandlers.RelationHandler):

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        id_svc = sunbeam_id_svc.IdentityServiceProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            id_svc.on.ready_identity_service_clients,
            self._on_identity_service_ready)
        return id_svc

    def _on_identity_service_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        return True


class CloudCredentialsProvidesHandler(sunbeam_rhandlers.RelationHandler):

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for a Cloud Credentials relation."""
        logger.debug("Setting up Cloud Credentials event handler")
        id_svc = sunbeam_cc_svc.CloudCredentialsProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            id_svc.on.ready_cloud_credentials_clients,
            self._on_cloud_credentials_ready)
        return id_svc

    def _on_cloud_credentials_ready(self, event) -> None:
        """Handles cloud credentials change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a username)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        return True


class FernetKeysUpdatedEvent(RelationEvent):
    """This local event triggered if fernet keys were updated."""

    def get_fernet_keys(self) -> Dict[str, str]:
        """Retrieve the fernet keys from app data."""
        return json.loads(
            self.relation.data[self.relation.app].get(FERNET_KEYS_KEY, "{}")
        )


class HeartbeatEvent(HookEvent):
    """This local event triggered regularly as a wake up call."""


class KeystoneEvents(CharmEvents):
    """Custom local events."""
    fernet_keys_updated = EventSource(FernetKeysUpdatedEvent)
    heartbeat = EventSource(HeartbeatEvent)


class KeystoneInterface(Object):

    def __init__(self, charm):
        super().__init__(charm, 'keystone-peers')
        self.charm = charm
        self.framework.observe(
            self.charm.on.peers_relation_changed,
            self._on_peer_data_changed
        )

    def _on_peer_data_changed(self, event: RelationChangedEvent):
        """
        Check the peer data updates for updated fernet keys.

        Then we can pull the keys from the app data,
        and tell the local charm to write them to disk.
        """
        old_data = event.relation.data[self.charm.unit].get(
            FERNET_KEYS_KEY, ''
        )
        data = self.charm.peers.get_app_data(FERNET_KEYS_KEY) or ''

        # only launch the event if the data has changed
        # and there there are actually keys
        # (not just an empty dictionary string "{}")
        if data and data != old_data and json.loads(data):
            event.relation.data[self.charm.unit].update(
                {FERNET_KEYS_KEY: data}
            )
            # use an event here so we can defer it
            # if keystone isn't bootstrapped yet
            self.charm.on.fernet_keys_updated.emit(
                event.relation, app=event.app, unit=event.unit
            )

    def distribute_fernet_keys(self, keys: Dict[str, str]):
        """
        Trigger a fernet key distribution.

        This is achieved by simply saving it to the app data here,
        which will trigger the peer data changed event across all the units.
        """
        self.charm.peers.set_app_data({
            FERNET_KEYS_KEY: json.dumps(keys),
        })


class KeystonePasswordManager(Object):
    """Helper for management of keystone credential passwords."""

    def __init__(self,
                 charm: ops.charm.CharmBase,
                 interface: OperatorPeers):
        self.charm = charm
        self.interface = interface

    def store(self, username: str, password: str):
        """Store username and password."""
        logging.debug(f"Storing password for {username}")
        self.interface.set_app_data({
            f"password_{username}": password,
        })

    def retrieve(self, username: str) -> str:
        """Retrieve persisted password for provided username"""
        if not self.interface:
            return None
        password = self.interface.get_app_data(f"password_{username}")
        return str(password) if password else None

    def retrieve_or_set(self, username: str) -> str:
        """Retrieve or setup a password for a user.

        New passwords will only be created by the lead unit of the
        application.
        """
        password = self.retrieve(username)
        if not password and self.charm.unit.is_leader():
            password = pwgen.pwgen(12)
            self.store(
                username,
                password
            )
        return password


class KeystoneOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    on = KeystoneEvents()
    _state = StoredState()
    _authed = False
    service_name = "keystone"
    wsgi_admin_script = '/usr/bin/keystone-wsgi-admin'
    wsgi_public_script = '/usr/bin/keystone-wsgi-public'
    service_port = 5000
    mandatory_relations = {
        'database',
        'ingress-public'
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.keystone_manager = manager.KeystoneManager(
            self,
            KEYSTONE_CONTAINER)
        self._state.set_default(admin_domain_name='admin_domain')
        self._state.set_default(admin_domain_id=None)
        self._state.set_default(default_domain_id=None)
        self._state.set_default(service_project_id=None)
        self.peer_interface = KeystoneInterface(self)

        self.framework.observe(
            self.on.fernet_keys_updated,
            self._on_fernet_keys_updated
        )
        self.framework.observe(self.on.heartbeat, self._on_heartbeat)
        self._launch_heartbeat()

        self.framework.observe(
            self.on.get_admin_password_action,
            self._get_admin_password_action
        )

        self.framework.observe(
            self.on.get_admin_account_action,
            self._get_admin_account_action
        )

        self.password_manager = KeystonePasswordManager(self, self.peers)

        self.framework.observe(
            self.on.get_service_account_action,
            self._get_service_account_action
        )

    def _get_admin_password_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail('Please run action on lead unit.')
            return
        event.set_results({"password": self.admin_password})

    def _get_admin_account_action(self, event: ActionEvent) -> None:
        """Get details for the admin account.

        This action handler will provide a full set of details
        to access the cloud using the admin account.
        """
        if not self.unit.is_leader():
            event.fail('Please run action on lead unit.')
            return
        openrc = f"""# openrc for access to OpenStack
export OS_AUTH_URL={self.public_endpoint}
export OS_USERNAME={self.admin_user}
export OS_PASSWORD={self.admin_password}
export OS_PROJECT_DOMAIN_NAME={self.admin_domain_name}
export OS_USER_DOMAIN_NAME={self.admin_domain_name}
export OS_PROJECT_NAME=admin
export OS_IDENTITY_API_VERSION=3
export OS_AUTH_VERSION=3
        """
        event.set_results({
            "username": self.admin_user,
            "password": self.admin_password,
            "user-domain-name": self.admin_domain_name,
            "project-name": "admin",
            "project-domain-name": self.admin_domain_name,
            "region": self.model.config['region'],
            "internal-endpoint": self.internal_endpoint,
            "public-endpoint": self.public_endpoint,
            "api-version": 3,
            "openrc": openrc,
        })

    def _launch_heartbeat(self):
        """
        Launch another process that will wake up the charm every 5 minutes.

        Used to auto schedule fernet key rotation.
        """
        # check if already running
        if subprocess.call(['pgrep', '-f', 'heartbeat']) == 0:
            return

        logger.debug("Launching the heartbeat")
        subprocess.Popen(
            ["./src/heartbeat.sh"],
            cwd=os.environ["JUJU_CHARM_DIR"],
        )

    def _on_fernet_keys_updated(self, event: FernetKeysUpdatedEvent):
        if not self.bootstrapped():
            event.defer()
            return

        keys = event.get_fernet_keys()
        if keys:
            self.keystone_manager.write_fernet_keys(keys)

    def _on_heartbeat(self, _event):
        """
        This should be called regularly.

        It will check if it's time to rotate the fernet keys,
        and perform the rotation and key distribution if it is time.
        """
        # Only rotate and distribute keys from the leader unit.
        if not self.unit.is_leader():
            return

        # if we're not set up, then don't try rotating keys
        if not self.bootstrapped():
            return

        # minimum allowed for max_keys is 3
        max_keys = max(self.model.config['fernet-max-active-keys'], 3)
        exp = self.model.config['token-expiration']
        exp_window = self.model.config['allow-expired-window']
        rotation_seconds = (exp + exp_window) / (max_keys - 2)

        # last time the fernet keys were rotated, in seconds since the epoch
        last_rotation: Optional[str] = (
            self.peers.get_app_data(LAST_FERNET_KEY_ROTATION_KEY)
        )
        now: int = int(time.time())

        if (
            last_rotation is None or
            now - int(last_rotation) >= rotation_seconds
        ):
            self._rotate_fernet_keys()
            self.peers.set_app_data({LAST_FERNET_KEY_ROTATION_KEY: str(now)})

    def _rotate_fernet_keys(self):
        """
        Rotate fernet keys and trigger distribution.

        If this is run on a non-leader unit, it's a noop.
        Keys should only ever be rotated and distributed from a single unit.
        """
        if not self.unit.is_leader():
            return
        self.keystone_manager.rotate_fernet_keys()
        self.peer_interface.distribute_fernet_keys(
            self.keystone_manager.read_fernet_keys()
        )

    def get_relation_handlers(self, handlers=None) -> List[
            sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler('identity-service', handlers):
            self.id_svc = IdentityServiceProvidesHandler(
                self,
                'identity-service',
                self.register_service,
            )
            handlers.append(self.id_svc)

        if self.can_add_handler('identity-credentials', handlers):
            self.cc_svc = CloudCredentialsProvidesHandler(
                self,
                'identity-credentials',
                self.add_credentials,
            )
            handlers.append(self.cc_svc)

        return super().get_relation_handlers(handlers)

    @property
    def config_contexts(self) -> List[sunbeam_contexts.ConfigContext]:
        """Configuration adapters for the operator."""
        return [
            KeystoneConfigAdapter(self, 'ks_config'),
            KeystoneLoggingAdapter(self, 'ks_logging'),
            sunbeam_contexts.CharmConfigContext(self, 'options')]

    @property
    def container_configs(self):
        _cconfigs = super().container_configs
        _cconfigs.extend([
            sunbeam_core.ContainerConfigFile(
                LOGGING_CONF,
                'keystone',
                'keystone')])
        return _cconfigs

    def register_service(self, event):
        if not self._state.bootstrapped:
            event.defer()
            return
        if not self.unit.is_leader():
            return
        relation = self.model.get_relation(
            event.relation_name,
            event.relation_id)
        binding = self.framework.model.get_binding(relation)
        ingress_address = str(binding.network.ingress_address)
        service_domain = self.keystone_manager.create_domain(
            name='service_domain',
            may_exist=True)
        service_project = self.keystone_manager.get_project(
            name=self.service_project,
            domain=service_domain)
        admin_domain = self.keystone_manager.get_domain(
            name='admin_domain')
        admin_project = self.keystone_manager.get_project(
            name='admin',
            domain=admin_domain)
        admin_user = self.keystone_manager.get_user(
            name=self.model.config['admin-user'],
            project=admin_project,
            domain=admin_domain)
        admin_role = self.keystone_manager.create_role(
            name=self.admin_role,
            may_exist=True)
        for ep_data in event.service_endpoints:
            service_username = 'svc_{}'.format(
                event.client_app_name.replace('-', '_'))
            service_password = self.password_manager.retrieve_or_set(
                service_username
            )
            service_user = self.keystone_manager.create_user(
                name=service_username,
                password=service_password,
                domain=service_domain.id,
                may_exist=True)
            self.keystone_manager.grant_role(
                role=admin_role,
                user=service_user,
                project=service_project,
                may_exist=True)
            service = self.keystone_manager.create_service(
                name=ep_data['service_name'],
                service_type=ep_data['type'],
                description=ep_data['description'],
                may_exist=True)
            for interface in ['admin', 'internal', 'public']:
                self.keystone_manager.create_endpoint(
                    service=service,
                    interface=interface,
                    url=ep_data[f'{interface}_url'],
                    region=event.region,
                    may_exist=True)
            self.id_svc.interface.set_identity_service_credentials(
                event.relation_name,
                event.relation_id,
                'v3',
                ingress_address,
                self.default_public_ingress_port,
                'http',
                ingress_address,
                self.default_public_ingress_port,
                'http',
                ingress_address,
                self.default_public_ingress_port,
                'http',
                admin_domain,
                admin_project,
                admin_user,
                service_domain,
                service_password,
                service_project,
                service_user,
                self.internal_endpoint,
                self.admin_endpoint,
                self.public_endpoint)

    def add_credentials(self, event):
        """

        :param event:
        :return:
        """
        if not self.unit.is_leader():
            logger.debug('Current unit is not the leader unit, deferring '
                         'credential creation to leader unit.')
            return

        if not self.bootstrapped():
            logger.debug('Keystone is not bootstrapped, deferring credential '
                         'creation until after bootstrap.')
            event.defer()
            return

        relation = self.model.get_relation(
            event.relation_name,
            event.relation_id)
        binding = self.framework.model.get_binding(relation)
        ingress_address = str(binding.network.ingress_address)
        service_domain = self.keystone_manager.create_domain(
            name='service_domain',
            may_exist=True)
        service_project = self.keystone_manager.get_project(
            name=self.service_project,
            domain=service_domain)
        user_password = self.password_manager.retrieve_or_set(event.username)
        service_user = self.keystone_manager.create_user(
            name=event.username,
            password=user_password,
            domain=service_domain.id,
            may_exist=True)
        admin_role = self.keystone_manager.create_role(
            name=self.admin_role,
            may_exist=True)
        # TODO(wolsen) let's not always grant admin role!
        self.keystone_manager.grant_role(
            role=admin_role,
            user=service_user,
            project=service_project,
            may_exist=True)

        self.cc_svc.interface.set_cloud_credentials(
            relation_name=event.relation_name,
            relation_id=event.relation_id,
            api_version='3',
            auth_host=ingress_address,
            auth_port=self.default_public_ingress_port,
            auth_protocol='http',
            internal_host=ingress_address,  # XXX(wolsen) internal address?
            internal_port=self.default_public_ingress_port,
            internal_protocol='http',
            username=service_user.name,
            password=user_password,
            project_name=service_project.name,
            project_id=service_project.id,
            user_domain_name=service_domain.name,
            user_domain_id=service_domain.id,
            project_domain_name=service_domain.name,
            project_domain_id=service_domain.id,
            region=self.model.config['region'],  # XXX(wolsen) region matters?
        )

    def _get_service_account_action(self, event: ActionEvent) -> None:
        """Create/get details for a service account.

        This action handler will create a new services account
        for the provided username.  This account can be used
        to provide access to OpenStack services from outside
        of the Charmed deployment.
        """
        if not self.unit.is_leader():
            event.fail('Please run action on lead unit.')
            return

        # TODO: refactor into general helper method.
        username = event.params['username']
        service_domain = self.keystone_manager.create_domain(
            name='service_domain',
            may_exist=True)
        service_project = self.keystone_manager.get_project(
            name=self.service_project,
            domain=service_domain)
        user_password = self.password_manager.retrieve_or_set(username)
        service_user = self.keystone_manager.create_user(
            name=username,
            password=user_password,
            domain=service_domain.id,
            may_exist=True)
        admin_role = self.keystone_manager.create_role(
            name=self.admin_role,
            may_exist=True)
        # TODO(wolsen) let's not always grant admin role!
        self.keystone_manager.grant_role(
            role=admin_role,
            user=service_user,
            project=service_project,
            may_exist=True)

        event.set_results({
            "username": username,
            "password": user_password,
            "user-domain-name": service_domain.name,
            "project-name": service_project.name,
            "project-domain-name": service_domain.name,
            "region": self.model.config['region'],
            "internal-endpoint": self.internal_endpoint,
            "public-endpoint": self.public_endpoint,
            "api-version": 3
        })

    @property
    def default_public_ingress_port(self):
        return 5000

    @property
    def default_domain_id(self):
        return self._state.default_domain_id

    @property
    def admin_domain_name(self):
        return self._state.admin_domain_name

    @property
    def admin_domain_id(self):
        return self._state.admin_domain_id

    @property
    def admin_password(self) -> str:
        """Retrieve the password for the Admin user."""
        return self.password_manager.retrieve_or_set(self.admin_user)

    @property
    def admin_user(self):
        return self.model.config['admin-user']

    @property
    def admin_role(self):
        return self.model.config['admin-role']

    @property
    def charm_user(self):
        """The admin user specific to the charm.

        This is a special admin user reserved for the charm to interact with
        keystone.
        """
        return '_charm-keystone-admin'

    @property
    def charm_password(self) -> str:
        """The password for the charm admin user."""
        return self.password_manager.retrieve_or_set(self.charm_user)

    @property
    def service_project(self):
        return self.model.config['service-tenant']

    @property
    def service_project_id(self):
        return self._state.service_project_id

    @property
    def admin_endpoint(self):
        admin_hostname = self.model.config.get('os-admin-hostname')
        if not admin_hostname:
            admin_hostname = self.model.get_binding(
                "identity-service"
            ).network.ingress_address
        return f'http://{admin_hostname}:{self.service_port}'

    @property
    def internal_endpoint(self):
        if self.ingress_internal and self.ingress_internal.url:
            return self.ingress_internal.url

        internal_hostname = self.model.config.get('os-internal-hostname')
        if not internal_hostname:
            internal_hostname = self.model.get_binding(
                "identity-service"
            ).network.ingress_address
        return f'http://{internal_hostname}:{self.service_port}'

    @property
    def public_endpoint(self):
        if self.ingress_public and self.ingress_public.url:
            return self.ingress_public.url

        address = self.public_ingress_address
        if not address:
            address = self.model.get_binding(
                'identity-service'
            ).network.ingress_address
        return f'http://{address}:{self.service_port}'

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return f'http://localhost:{self.default_public_ingress_port}/v3'

    def _do_bootstrap(self) -> bool:
        """
        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
         2. Bootstrap the keystone users service
         3. Setup the fernet tokens
        """
        if not super()._do_bootstrap():
            return False

        if self.unit.is_leader():
            try:
                self.keystone_manager.setup_keystone()
            except (ops.pebble.ExecError, ops.pebble.ConnectionError) as error:
                logger.exception('Failed to bootstrap')
                logger.exception(error)
                return False

            try:
                self.keystone_manager.setup_initial_projects_and_users()
            except Exception:
                # keystone might fail with Internal server error, not
                # sure of exact exceptions to be caught. List below that
                # are observed:
                # keystoneauth1.exceptions.connection.ConnectFailure
                logger.exception('Failed to setup projects and users')
                return False

        self.unit.status = model.MaintenanceStatus('Starting Keystone')
        return True

    def _ingress_changed(self, event: ops.framework.EventBase) -> None:
        """Ingress changed callback.

        Invoked when the data on the ingress relation has changed. This will
        update the keystone endpoints, and then call the configure_charm.
        """
        logger.debug('Received an ingress_changed event')
        if self.bootstrapped():
            self.keystone_manager.update_service_catalog_for_keystone()
        self.configure_charm(event)


class KeystoneXenaOperatorCharm(KeystoneOperatorCharm):

    openstack_release = 'xena'


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(KeystoneXenaOperatorCharm, use_juju_for_storage=True)
