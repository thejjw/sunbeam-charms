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

from ops.main import main
from ops.framework import StoredState
from ops import model

from utils import manager
import advanced_sunbeam_openstack.cprocess as sunbeam_cprocess
import advanced_sunbeam_openstack.adapters as sunbeam_adapters
import advanced_sunbeam_openstack.core as sunbeam_core

logger = logging.getLogger(__name__)

KEYSTONE_CONTAINER = "keystone"


KEYSTONE_CONF = '/etc/keystone/keystone.conf'
LOGGING_CONF = '/etc/keystone/logging.conf'


class KeystoneLoggingAdapter(sunbeam_adapters.ConfigAdapter):

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


class KeystoneConfigAdapter(sunbeam_adapters.ConfigAdapter):

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
            'admin_port': config['admin-port'],
            'public_port': config['service-port'],
            'debug': config['debug'],
            'token_expiration': config['token-expiration'],
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


class KeystoneOperatorCharm(sunbeam_core.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    _authed = False
    service_name = "keystone"
    wsgi_admin_script = '/usr/bin/keystone-wsgi-admin'
    wsgi_public_script = '/usr/bin/keystone-wsgi-public'

    def __init__(self, framework):
        super().__init__(framework)
        self.keystone_manager = manager.KeystoneManager(self)
        self._state.set_default(admin_domain_name='admin_domain')
        self._state.set_default(admin_domain_id=None)
        self._state.set_default(default_domain_id=None)
        self._state.set_default(service_project_id=None)
        self.adapters.add_config_adapters([
            KeystoneConfigAdapter(self, 'ks_config'),
            KeystoneLoggingAdapter(self, 'ks_logging')])

    @property
    def container_configs(self):
        _cconfigs = super().container_configs
        _cconfigs.extend([
            sunbeam_core.ContainerConfigFile(
                [KEYSTONE_CONTAINER],
                LOGGING_CONF,
                'keystone',
                'keystone')])
        return _cconfigs

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

    def _do_bootstrap(self):
        """
        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
         2. Bootstrap the keystone users service
         3. Setup the fernet tokens
        """
        super()._do_bootstrap()
        try:
            container = self.unit.get_container(self.wsgi_container_name)
            self.keystone_manager.setup_keystone(container)
        except sunbeam_cprocess.ContainerProcessError:
            logger.exception('Failed to bootstrap')
            self._state.bootstrapped = False
            return
        self.keystone_manager.setup_initial_projects_and_users()
        self.unit.status = model.MaintenanceStatus('Starting Keystone')


class KeystoneVictoriaOperatorCharm(KeystoneOperatorCharm):

    openstack_release = 'victoria'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(KeystoneVictoriaOperatorCharm, use_juju_for_storage=True)
