#!/usr/bin/env python3
# Copyright 2021 Billy Olsen
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from charms.mysql.v1.mysql import MySQLConsumer

from ops.charm import CharmBase
from ops.charm import PebbleReadyEvent

from ops.main import main
from ops.framework import StoredState
from ops import model

from utils import contexts
from utils import manager
from utils.cprocess import check_output
from utils.cprocess import ContainerProcessError
from utils.templating import SidecarConfigRenderer

logger = logging.getLogger(__name__)

KEYSTONE_CONTAINER = "keystone"


KEYSTONE_CONF = '/etc/keystone/keystone.conf'
LOGGING_CONF = '/etc/keystone/logging.conf'
DATABASE_CONF = '/etc/keystone/database.conf'
KEYSTONE_WSGI_CONF = '/etc/apache2/sites-available/wsgi-keystone.conf'


class KeystoneOperatorCharm(CharmBase):
    """Charm the service."""

    _state = StoredState()
    _authed = False

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.keystone_pebble_ready,
                               self._on_keystone_pebble_ready)
        self.framework.observe(self.on.config_changed,
                               self._on_config_changed)

        # Register the database consumer and register for events
        self.db = MySQLConsumer(self, 'keystone-db', {"mysql": ">=8"})
        self.framework.observe(self.on.keystone_db_relation_changed,
                               self._on_database_changed)

        self.ingress_public = IngressRequires(self, {
            'service-hostname': self.model.config['os-public-hostname'],
            'service-name': self.app.name,
            'service-port': self.model.config['service-port'],
        })
        self.keystone_manager = manager.KeystoneManager(self)

        # TODO(wolsen) how to determine the current release? Will likely need
        #  to peak inside the container
        self.os_config_renderer = SidecarConfigRenderer('src/templates',
                                                        'victoria')
        self._register_configs(self.os_config_renderer)
        self._state.set_default(bootstrapped=False)
        self._state.set_default(db_ready=False)
        self._state.set_default(admin_domain_name='admin_domain')
        self._state.set_default(admin_domain_id=None)
        self._state.set_default(default_domain_id=None)
        self._state.set_default(service_project_id=None)

    def _register_configs(self, renderer: SidecarConfigRenderer) -> None:
        """

        """
        renderer.register(KEYSTONE_CONF, contexts.KeystoneContext(self),
                          containers=[KEYSTONE_CONTAINER],
                          user='keystone', group='keystone')
        renderer.register(LOGGING_CONF, contexts.KeystoneLoggingContext(self),
                          containers=[KEYSTONE_CONTAINER],
                          user='keystone', group='keystone')
        renderer.register(DATABASE_CONF,
                          contexts.DatabaseContext(self, 'keystone-db'),
                          containers=[KEYSTONE_CONTAINER],
                          user='keystone', group='keystone')
        renderer.register(KEYSTONE_WSGI_CONF,
                          contexts.WSGIWorkerConfigContext(self),
                          containers=[KEYSTONE_CONTAINER],
                          user='root', group='root')

    def _on_database_changed(self, event) -> None:
        """Handles database change events."""
        # self.unit.status = model.MaintenanceStatus('Updating database '
        #                                            'configuration')
        databases = self.db.databases()
        logger.info(f'Received databases: {databases}')

        if not databases:
            logger.info(f'Requesting a new database...')
            # The mysql-k8s operator creates a database using the relation
            # information in the form of:
            #   db_{relation_id}_{partial_uuid}_{name_suffix}
            # where name_suffix defaults to "". Specify it to the name of the
            # current app to make it somewhat understandable as to what this
            # database actually is for.
            # NOTE(wolsen): database name cannot contain a '-'
            name_suffix = self.app.name.replace('-', '_')
            self.db.new_database(name_suffix=name_suffix)
            return

        credentials = self.db.credentials()
        logger.info(f'Received credentials: {credentials}')
        self._state.db_ready = True
        self._do_bootstrap()

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
    def admin_password(self):
        # TODO(wolsen) password stuff
        return 'abc123'

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
    def charm_password(self):
        # TODO
        return 'abc123'

    @property
    def service_project(self):
        return self.model.config['service-tenant']

    @property
    def service_project_id(self):
        return self._state.service_project_id

    @property
    def admin_endpoint(self):
        admin_hostname = self.model.config['os-admin-hostname']
        admin_port = self.model.config['admin-port']
        return f'http://{admin_hostname}:{admin_port}/v3'

    @property
    def internal_endpoint(self):
        internal_hostname = self.model.config['os-internal-hostname']
        service_port = self.model.config['service-port']
        return f'http://{internal_hostname}:{service_port}/v3'

    @property
    def public_endpoint(self):
        public_hostname = self.model.config['os-public-hostname']
        return f'http://{public_hostname}:5000/v3'

    @property
    def db_ready(self):
        """Returns True if the remote database has been configured and is
        ready for access from the local service.

        :returns: True if the database is ready to be accessed, False otherwise
        :rtype: bool
        """
        return self._state.db_ready

    def is_bootstrapped(self):
        """Returns True if the instance is bootstrapped.

        :returns: True if the keystone service has been bootstrapped,
                  False otherwise
        :rtype: bool
        """
        return self._state.bootstrapped

    def _do_bootstrap(self):
        """Checks the services to see which services need to run depending
        on the current state.

        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the keystone database
         2. Bootstrap the keystone users service
         3. Setup the fernet tokens
        """
        if self.is_bootstrapped():
            logger.debug('Keystone is already bootstrapped')
            return

        if not self.db_ready:
            logger.debug('Database not ready, not bootstrapping')
            self.unit.status = model.BlockedStatus('Waiting for database')
            return

        if not self.unit.is_leader():
            logger.debug('Deferring bootstrap to leader unit')
            self.unit.status = model.BlockedStatus('Waiting for leader to '
                                                   'bootstrap keystone')
            return

        container = self.unit.get_container('keystone')
        if not container:
            logger.debug('Keystone container is not ready. Deferring bootstrap')
            return

        # Write the config files to the container
        self.os_config_renderer.write_all(container)

        try:
            check_output(container, 'a2ensite wsgi-keystone && sleep 1')
        except ContainerProcessError:
            logger.exception('Failed to enable wsgi-keystone site in apache')
            # ignore for now - pebble is raising an exited too quickly, but it
            # appears to work properly.

        try:
            self.keystone_manager.setup_keystone(container)
        except ContainerProcessError:
            logger.exception('Failed to bootstrap')
            self._state.bootstrapped = False
            return

        self.unit.status = model.MaintenanceStatus('Starting Keystone')
        service = container.get_service('keystone-wsgi')
        if service.is_running():
            container.stop('keystone-wsgi')

        container.start('keystone-wsgi')

        self.keystone_manager.setup_initial_projects_and_users()

        self.unit.status = model.ActiveStatus()
        self._state.bootstrapped = True

    def _on_keystone_pebble_ready(self, event: PebbleReadyEvent) -> None:
        """Invoked when the keystone bootstrap init container is ready.

        When invoked, the Pebble service is running in the container and ready
        for bootstrap. The bootstrap sequence consists of creating the initial
        keystone database and performing initial setup of the admin
        credentials.
        """
        container = event.workload
        logger.debug('Updating keystone bootstrap layer to create the '
                     'keystone database')

        container.add_layer('keystone', self._keystone_layer(), combine=True)
        logger.debug(f'Plan: {container.get_plan()}')
        self._do_bootstrap()

    def _keystone_layer(self) -> dict:
        """Keystone layer definition.

        :returns: pebble layer configuration for keystone services
        :rtype: dict
        """
        return {
            'summary': 'keystone layer',
            'description': 'pebble config layer for keystone',
            'services': {
                'keystone-wsgi': {
                    'override': 'replace',
                    'summary': 'Keystone Identity',
                    'command': '/usr/sbin/apache2ctl -DFOREGROUND',
                    'startup': 'disabled',
                },
            },
        }

    def _on_config_changed(self, _):
        """Just an example to show how to deal with changed configuration.
        """
        logger.debug('config changed event')
        if self._state.bootstrapped:
            self.keystone_manager.update_service_catalog_for_keystone()


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(KeystoneOperatorCharm, use_juju_for_storage=True)
