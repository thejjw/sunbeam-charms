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

import ops.pebble
from ops import framework
from ops.model import MaintenanceStatus

from keystoneauth1 import session
from keystoneauth1.identity import v3
from keystoneclient.v3 import client
from keystoneclient.v3.domains import Domain
from keystoneclient.v3.endpoints import Endpoint
from keystoneclient.v3.projects import Project
from keystoneclient.v3.regions import Region
from keystoneclient.v3.roles import Role
from keystoneclient.v3.services import Service
from keystoneclient.v3.users import User

import advanced_sunbeam_openstack.guard as sunbeam_guard

import logging
import typing

logger = logging.getLogger(__name__)


class KeystoneException(Exception):
    pass


class KeystoneManager(framework.Object):
    """

    """
    def __init__(self, charm, container_name):
        super().__init__(charm, 'keystone-manager')
        self.charm = charm
        self.container_name = container_name
        self._api = None

    def run_cmd(self, cmd, exception_on_error=True):
        pebble_handler = self.charm.get_named_pebble_handler(
            self.container_name)
        pebble_handler.execute(
            cmd,
            exception_on_error=True)

    @property
    def api(self):
        """
        Returns the current api reference or creates a new one.

        TODO(wolsen): All of the direct interaction with keystone belongs in
         an Adapter class which can handle v3 as well as future versions.
        """
        if self._api:
            return self._api

        # TODO(wolsen) use appropriate values rather than these
        auth = v3.Password(
            auth_url="http://localhost:5000/v3",
            username=self.charm.charm_user,
            password=self.charm.charm_password,
            system_scope='all',
            project_domain_name='Default',
            user_domain_name='Default',
        )
        keystone_session = session.Session(auth=auth)
        self._api = client.Client(session=keystone_session,
                                  endpoint_override='http://localhost:5000/v3')
        return self._api

    @property
    def admin_endpoint(self):
        return self.charm.admin_endpoint

    @property
    def internal_endpoint(self):
        return self.charm.internal_endpoint

    @property
    def public_endpoint(self):
        return self.charm.public_endpoint

    @property
    def regions(self):
        # split regions and strip out empty regions
        regions = [r for r in self.charm.model.config['region'].split() if r]
        return regions

    def setup_keystone(self):
        """Runs the keystone setup process for first time configuration.

        Runs through the keystone setup process for initial installation and
        configuration. This involves creating the database, setting up fernet
        repositories for tokens and credentials, and bootstrapping the initial
        keystone service.
        """
        with sunbeam_guard.guard(self.charm, 'Initializing Keystone'):
            self._sync_database()
            self._fernet_setup()
            self._credential_setup()
            self._bootstrap()

    def _set_status(self, status: str, app: bool = False) -> None:
        """Sets the status to the specified status string.
        By default, the status is set on the individual unit but can be set
        for the whole application if app is set to True.

        :param status: the status to set
        :type status: str
        :param app: whether to set the status for the application or the unit
        :type app: bool
        :return: None
        """
        if app:
            target = self.charm.app
        else:
            target = self.charm.unit

        target.status = MaintenanceStatus(status)

    def _sync_database(self):
        """Syncs the database using the keystone-manage db_sync

        The database is synchronized using the keystone-manage db_sync command.
        Database configuration information is retrieved from configuration
        files.

        :raises: KeystoneException when the database sync fails.
        """
        try:
            self._set_status('Syncing database')
            logger.info("Syncing database...")
            self.run_cmd([
                'sudo', '-u', 'keystone',
                'keystone-manage', '--config-dir',
                '/etc/keystone', 'db_sync'])
        except ops.pebble.ExecError:
            logger.exception('Error occurred synchronizing the database.')
            raise KeystoneException('Database sync failed')

    def _fernet_setup(self):
        """Sets up the fernet token store in the specified container.

        :raises: KeystoneException when a failure occurs setting up the fernet
                 token store
        """
        try:
            self._set_status('Setting up fernet tokens')
            logger.info("Setting up fernet tokens...")
            self.run_cmd([
                'sudo', '-u', 'keystone',
                'keystone-manage', 'fernet_setup',
                '--keystone-user', 'keystone',
                '--keystone-group', 'keystone'])
        except ops.pebble.ExecError:
            logger.exception('Error occurred setting up fernet tokens')
            raise KeystoneException('Fernet setup failed.')

    def _credential_setup(self):
        """

        """
        try:
            self._set_status('Setting up credentials')
            logger.info("Setting up credentials...")
            self.run_cmd([
                'sudo', '-u', 'keystone',
                'keystone-manage', 'credential_setup'])
        except ops.pebble.ExecError:
            logger.exception('Error occurred during credential setup')
            raise KeystoneException('Credential setup failed.')

    def _bootstrap(self):
        """

        """
        try:
            self._set_status('Bootstrapping Keystone')
            logger.info('Bootstrapping keystone service')

            # NOTE(wolsen) in classic keystone charm, there's a comment about
            # enabling immutable roles for this. This is unnecessary as it is
            # now the default behavior for keystone-manage bootstrap.
            self.run_cmd([
                'keystone-manage', 'bootstrap',
                '--bootstrap-username', self.charm.charm_user,
                '--bootstrap-password', self.charm.charm_password,
                '--bootstrap-project-name', 'admin',
                '--bootstrap-role-name', self.charm.admin_role,
                '--bootstrap-service-name', 'keystone',
                '--bootstrap-admin-url', self.admin_endpoint,
                '--bootstrap-public-url', self.public_endpoint,
                '--bootstrap-internal-url', self.internal_endpoint,
                '--bootstrap-region-id', self.regions[0]])
        except ops.pebble.ExecError:
            logger.exception('Error occurred bootstrapping keystone service')
            raise KeystoneException('Bootstrap failed')

    def setup_initial_projects_and_users(self):
        """

        """
        with sunbeam_guard.guard(self.charm,
                                 'Setting up initial projects and users'):
            self._setup_admin_accounts()
            self._setup_service_accounts()
            self.update_service_catalog_for_keystone()

    def _setup_admin_accounts(self):
        """

        """
        # Get the default domain id
        default_domain = self.get_domain('default')
        logger.debug(f'Default domain id: {default_domain.id}')
        self.charm._state.default_domain_id = default_domain.id  # noqa

        # Get the admin domain id
        admin_domain = self.create_domain(name='admin_domain',
                                          may_exist=True)
        logger.debug(f'Admin domain id: {admin_domain.id}')
        self.charm._state.admin_domain_id = admin_domain.id  # noqa
        self.charm._state.admin_domain_name = admin_domain.name  # noqa

        # Ensure that we have the necessary projects: admin and service
        admin_project = self.create_project(name='admin', domain=admin_domain,
                                            may_exist=True)

        logger.debug('Ensuring admin user exists')
        admin_user = self.create_user(name=self.charm.admin_user,
                                      password=self.charm.admin_password,
                                      domain=admin_domain, may_exist=True)

        logger.debug('Ensuring roles exist for admin')
        # I seem to recall all kinds of grief between Member and member and
        # _member_ and inconsistencies in what other projects expect.
        member_role = self.create_role(name='member', may_exist=True)
        admin_role = self.create_role(name=self.charm.admin_role,
                                      may_exist=True)

        logger.debug('Granting roles to admin user')
        # Make the admin a member of the admin project
        self.grant_role(role=member_role, user=admin_user,
                        project=admin_project, may_exist=True)
        # Make the admin an admin of the admin project
        self.grant_role(role=admin_role, user=admin_user,
                        project=admin_project, may_exist=True)
        # Make the admin a domain-level admin
        self.grant_role(role=admin_role, user=admin_user,
                        domain=admin_domain, may_exist=True)

    def _setup_service_accounts(self):
        """

        """
        # Get the service domain id
        service_domain = self.create_domain(name='service_domain',
                                            may_exist=True)
        logger.debug(f'Service domain id: {service_domain.id}.')

        service_project = self.create_project(name=self.charm.service_project,
                                              domain=service_domain,
                                              may_exist=True)
        logger.debug(f'Service project id: {service_project.id}.')
        self.charm._state.service_project_id = service_project.id  # noqa

    def update_service_catalog_for_keystone(self):
        """

        """
        service = self.create_service(name='keystone', service_type='identity',
                                      description='Keystone Identity Service',
                                      may_exist=True)

        endpoints = {
            'admin': self.admin_endpoint,
            'internal': self.internal_endpoint,
            'public': self.public_endpoint,
        }

        for region in self.charm.model.config['region'].split():
            if not region:
                continue

            for interface, url in endpoints.items():
                self.create_endpoint(service=service, interface=interface,
                                     url=url, region=region, may_exist=True)

    def get_domain(self, name: str) -> 'Domain':
        """Returns the domain specified by the name, or None if a matching
        domain could not be found.

        :param name: the name of the domain
        :type name: str
        :rtype: 'Domain' or None
        """
        for domain in self.api.domains.list():
            if domain.name.lower() == name.lower():
                return domain

        return None

    def create_domain(self, name: str, description: str = 'Created by Juju',
                      may_exist: bool = False) -> 'Domain':
        """

        """
        if may_exist:
            domain = self.get_domain(name)
            if domain:
                logger.debug(f'Domain {name} already exists with domain '
                             f'id {domain.id}.')
                return domain

        domain = self.api.domains.create(name=name, description=description)
        logger.debug(f'Created domain {name} with id {domain.id}')
        return domain

    def create_project(self, name: str, domain: str,
                       description: str = 'Created by Juju',
                       may_exist: bool = False) -> 'Project':
        """

        """
        if may_exist:
            for project in self.api.projects.list(domain=domain):
                if project.name.lower() == name.lower():
                    logger.debug(f'Project {name} already exists with project '
                                 f'id {project.id}.')
                    return project

        project = self.api.projects.create(name=name, description=description,
                                           domain=domain)
        logger.debug(f'Created project {name} with id {project.id}')
        return project

    def get_project(self, name: str, domain: typing.Union[str, 'Domain']=None):
        """

        """
        projects = self.api.projects.list(domain=domain)
        for project in projects:
            if project.name.lower() == name.lower():
                return project
        return None

    def create_user(self, name: str, password: str, email: str = None,
                    project: 'Project'=None,
                    domain: 'Domain'=None,
                    may_exist: bool = False) -> 'User':
        """

        """
        if may_exist:
            user = self.get_user(name, project=project, domain=domain)
            if user:
                logger.debug(f'User {name} already exists with user '
                             f'id {user.id}.')
                return user

        user = self.api.users.create(name=name, default_project=project,
                                     domain=domain, password=password,
                                     email=email)
        logger.debug(f'Created user {user.name} with id {user.id}.')
        return user

    def get_user(self, name: str, project: 'Project'=None,
                 domain: typing.Union[str, 'Domain']=None) -> 'User':
        """

        """
        users = self.api.users.list(default_project=project, domain=domain)
        for user in users:
            if user.name.lower() == name.lower():
                return user

        return None

    def create_role(self, name: str,
                    domain: typing.Union['Domain', str]=None,
                    may_exist: bool = False) -> 'Role':
        """

        """
        if may_exist:
            role = self.get_role(name=name, domain=domain)
            if role:
                logger.debug(f'Role {name} already exists with role '
                             f'id {role.id}')
                return role

        role = self.api.roles.create(name=name, domain=domain)
        logger.debug(f'Created role {name} with id {role.id}.')
        return role

    def get_role(self, name: str,
                 domain: 'Domain' = None) -> 'Role':
        """

        """
        for role in self.api.roles.list(domain=domain):
            if role.name == name:
                return role

        return None

    def get_roles(self, user: 'User',
                  project: 'Project'=None,
                  domain: 'Project'=None) \
            -> typing.List['Role']:
        """

        """
        if project and domain:
            raise ValueError('Project and domain are mutually exclusive')
        if not project and not domain:
            raise ValueError('Project or domain must be specified')

        if project:
            roles = self.api.roles.list(user=user, project=project)
        else:
            roles = self.api.roles.list(user=user, domain=domain)

        return roles

    def grant_role(self, role: typing.Union['Role', str],
                   user: 'User',
                   project: typing.Union['Project', str]=None,
                   domain: typing.Union['Domain', str]=None,
                   may_exist: bool = False) -> 'Role':
        """

        """
        if project and domain:
            raise ValueError('Project and domain are mutually exclusive')
        if not project and not domain:
            raise ValueError('Project or domain must be specified')

        if domain:
            ctxt_str = f'domain {domain.name}'
        else:
            ctxt_str = f'project {project.name}'

        if may_exist:
            roles = self.get_roles(user=user, project=project, domain=domain)
            for r in roles:
                if role.id == r.id:
                    logger.debug(f'User {user.name} already has role '
                                 f'{role.name} for {ctxt_str}')
                    return r

        role = self.api.roles.grant(role=role, user=user, project=project,
                                    domain=domain)
        logger.debug(f'Granted user {user} role {role} for '
                     f'{ctxt_str}.')
        return role

    def create_region(self, name: str, description: str = None,
                      may_exist: bool = False) -> 'Region':
        """

        """
        if may_exist:
            for region in self.api.regions.list():
                if region.id == name:
                    logger.debug(f'Region {name} already exists.')
                    return region

        region = self.api.regions.create(id=name, description=description)
        logger.debug(f'Created region {name}.')
        return region

    def create_service(self, name: str, service_type: str,
                       description: str, owner: str = None,
                       may_exist: bool = False) -> 'Service':
        """

        """
        if may_exist:
            services = self.api.services.list(name=name, type=service_type)
            # TODO(wolsen) can we have more than one service with the same
            #  service name? I don't think so, so we'll just handle the first
            #  one for now.
            print("FOUND: {}".format(services))
            for service in services:
                logger.debug(f'Service {name} already exists with '
                             f'service id {service.id}.')
                return service

        service = self.api.services.create(name=name, type=service_type,
                                           description=description)
        logger.debug(f'Created service {service.name} with id {service.id}')
        return service

    def create_endpoint(self, service: 'Service', url: str, interface: str,
                        region: str, may_exist: bool = False) \
            -> 'Endpoint':
        """

        """
        ep_string = (f'{interface} endpoint for service {service} in '
                     f'region {region}')
        if may_exist:
            endpoints = self.api.endpoints.list(service=service,
                                                interface=interface,
                                                region=region)
            if endpoints:
                # NOTE(wolsen) if we have endpoints found, there should be only
                # one endpoint; but assert it to make sure
                assert len(endpoints) == 1
                endpoint = endpoints[0]
                if endpoint.url != url:
                    logger.debug(f'{ep_string} ({endpoint.url}) does '
                                 f'not match requested url ({url}). Updating.')
                    endpoint = self.api.endpoints.update(endpoint=endpoint,
                                                         url=url)
                    logger.debug(f'Endpoint updated to use {url}')
                else:
                    logger.debug(f'Endpoint {ep_string} already exists with '
                                 f'id {endpoint.id}')
                return endpoint

        endpoint = self.api.endpoints.create(service=service, url=url,
                                             interface=interface,
                                             region=region)
        logger.debug(f'Created endpoint {ep_string} with id {endpoint.id}')
        return endpoint
