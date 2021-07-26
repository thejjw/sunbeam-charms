# Copyright 2021, Canonical Ltd.
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

from ops import framework
import json
import logging

log = logging.getLogger(__name__)


class ContextGenerator(framework.Object):
    """Base class for all context generators"""
    interfaces = []
    related = False
    complete = False
    missing_data = []

    def __init__(self, charm, context_name):
        super().__init__(charm, context_name)
        self.charm = charm

    def __call__(self):
        raise NotImplementedError

    def context_complete(self, ctxt):
        """Check for missing data for the required context data.
        Set self.missing_data if it exists and return False.
        Set self.complete if no missing data and return True.
        """
        # Fresh start
        self.complete = False
        self.missing_data = []
        for k, v in ctxt.items(ctxt):
            if v is None or v == '':
                if k not in self.missing_data:
                    self.missing_data.append(k)

        if self.missing_data:
            self.complete = False
            log.debug(f"Missing required data: {' '.join(self.missing_data)}")
        else:
            self.complete = True
        return self.complete


class DatabaseContext(ContextGenerator):

    def __init__(self, charm, relation_name):
        super().__init__(charm, 'database_context')
        self.relation_name = relation_name

    def __call__(self):
        relation = self.charm.model.get_relation(self.relation_name)
        if not relation:
            log.error(f'Relation {self.relation_name} is not complete')
            return {}

        data = relation.data[relation.app]
        databases = data.get('databases')
        rdata = data.get('data')
        rdata = json.loads(rdata) if rdata else {}
        credentials = rdata.get('credentials')
        databases = json.loads(databases) if databases else []
        if not databases:
            log.error(f'No databases for {self.relation_name}')
            return {}

        # TODO(wolsen) do we need the port? We probably need to add
        #  mysql-router as part of this overall configuration
        ctxt = {
            'database_type': 'mysql+pymysql',
            'database': databases[0],
            'database_user': credentials.get('username'),
            'database_password': credentials.get('password'),
            'database_host': credentials.get('address'),
        }
        log.debug(f'Returning database context of: {ctxt}')
        return ctxt


class WSGIWorkerConfigContext(ContextGenerator):

    def __init__(self, charm):
        super().__init__(charm, 'WSGIWorkerConfigContext')

    def __call__(self, *args, **kwargs):
        return {
            'name': 'keystone',
            'admin_script': '/usr/bin/keystone-wsgi-admin',
            'public_script': '/usr/bin/keystone/wsgi-public',
        }


class KeystoneContext(ContextGenerator):

    def __init__(self, charm):
        super().__init__(charm, 'KeystoneContext')

    def __call__(self, *args, **kwargs):
        config = self.charm.model.config

        ctxt = {
            'api_version': 3,
            'admin_role': self.charm.admin_role,
            'service_tenant_id': self.charm.service_project_id,
            'admin_domain_name': self.charm.admin_domain_name,
            'admin_domain_id': self.charm.admin_domain_id,
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

        # TODO(wolsen) LDAP, password security compliance
        return ctxt


class KeystoneLoggingContext(ContextGenerator):

    def __init__(self, charm):
        super().__init__(charm, 'KeystoneLoggingContext')

    def __call__(self, *args, **kwargs):
        config = self.charm.model.config
        ctxt = {}
        if config['debug']:
            ctxt['root_level'] = 'DEBUG'
        log_level = config['log-level']
        if log_level in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            ctxt['log_level'] = log_level
        else:
            log.error('log-level must be one of the following values '
                      f'(DEBUG, INFO, WARNING, ERROR) not "{log_level}"')
            ctxt['log_level'] = None
        ctxt['log_file'] = '/var/log/keystone/keystone.log'
        return ctxt
