# Copyright 2023 Canonical Ltd.
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

"""Helper functions to interact with keystone."""

import logging
from typing import (
    Optional,
    Union,
)

from keystoneclient.v3.client import (
    Client,
)
from keystoneclient.v3.domains import (
    Domain,
)
from keystoneclient.v3.endpoints import (
    Endpoint,
)
from keystoneclient.v3.projects import (
    Project,
)
from keystoneclient.v3.regions import (
    Region,
)
from keystoneclient.v3.roles import (
    Role,
)
from keystoneclient.v3.services import (
    Service,
)
from keystoneclient.v3.users import (
    User,
)

logger = logging.getLogger(__name__)


class KeystoneExceptionError(Exception):
    """Error interacting with Keystone."""

    pass


class KeystoneClient:
    """Client to interact with keystone."""

    def __init__(self, api: Client):
        self.api = api

    def _convert_endpoint_to_dict(self, endpoint: Endpoint) -> dict:
        return {
            "id": endpoint.id,
            "service_id": endpoint.service_id,
            "interface": endpoint.interface,
            "region": endpoint.region,
            "url": endpoint.url,
            "enabled": endpoint.enabled,
        }

    def _convert_domain_to_dict(self, domain: Domain) -> dict:
        return {
            "id": domain.id,
            "name": domain.name,
            "description": domain.description,
            "enabled": domain.enabled,
        }

    def _convert_region_to_dict(self, region: Region) -> dict:
        return {
            "id": region.id,
            "description": region.description,
            "parent_region_id": region.parent_region_id,
        }

    def _convert_project_to_dict(self, project: Project) -> dict:
        return {
            "id": project.id,
            "name": project.name,
            "domain_id": project.domain_id,
            "description": project.description,
            "enabled": project.enabled,
            "is_domain": project.is_domain,
        }

    def _convert_user_to_dict(self, user: User) -> dict:
        return {
            "id": user.id,
            "name": user.name,
            "domain_id": user.domain_id,
            "enabled": user.enabled,
            "password_expires_at": user.password_expires_at,
        }

    def _convert_role_to_dict(self, role: Role) -> dict:
        return {
            "id": role.id,
            "name": role.name,
            "domain_id": role.domain_id,
            "description": role.description,
        }

    def get_domain_object(self, identifier: str) -> Optional[Domain]:
        """Returns domain object from domain name or id.

        Look for domain in domain list and return domain object.
        Returns None if domain does not exist.

        :param identifier: Domain name or id
        :type identifier: str
        :rtype: Domain | None
        """
        if identifier is None:
            return None

        domains = self.api.domains.list()
        logger.debug(f"Domains list: {domains}")
        if domains is None:
            return None
        for domain in domains:
            if (
                domain.id == identifier
                or domain.name.lower() == identifier.lower()
            ):
                logger.debug(
                    f"Domain object for domain {identifier}: {domain}"
                )
                return domain

        return None

    def get_project_object(
        self, identifier: str, domain: Optional[Union[Domain, str]] = None
    ) -> Optional[Project]:
        """Returns project object from name or id.

        Look for project in project list and return project object.
        Returns None if project does not exist.

        :param identifier: Project name or id
        :type identifier: str
        :param domain: Domain object, name or id
        :type name: Domain | str | None
        :rtype: Project | None
        :raises: KeystoneExceptionError
        """
        if identifier is None:
            return None

        if not isinstance(domain, Domain):
            domain = self.get_domain_object(domain)

        projects = self.api.projects.list(domain=domain)
        logger.debug(f"Projects list in domain {domain}: {projects}")
        if projects is None:
            return None
        projects_list = [
            project
            for project in projects
            if any(
                (
                    project.id == identifier,
                    project.name.lower() == identifier.lower(),
                )
            )
        ]

        count = len(projects_list)
        if count == 1:
            logger.debug(
                f"Project object for project {identifier}: {projects_list[0]}"
            )
            return projects_list[0]
        elif count > 1:
            raise KeystoneExceptionError(
                "More than one project with same name exists"
            )

        return None

    def get_user_object(
        self,
        identifier: str,
        domain: Optional[Union[Domain, str]] = None,
        project: Optional[Union[Project, str]] = None,
    ) -> Optional[User]:
        """Returns user object from name or id.

        Look for user in users list and return user object.
        Returns None if user does not exist.

        :param identifier: User name or id
        :type identifier: str
        :param domain: Domain object, name or id
        :type name: Domain | str | None
        :param project: Project object, name or id
        :type name: Project | str | None
        :rtype: User | None
        :raises: KeystoneExceptionError
        """
        if identifier is None:
            return None

        if not isinstance(domain, Domain):
            domain = self.get_domain_object(domain)

        if not isinstance(project, Project):
            # Do we need to differentiate project domain and user domain here??
            project = self.get_project_object(project, domain)

        users = self.api.users.list(domain=domain, default_project=project)
        logger.debug(
            f"Users list in domain {domain}, project {project}: {users}"
        )
        if users is None:
            return
        users_list = [
            user
            for user in users
            if any(
                (
                    identifier == user.id,
                    user.name.lower() == identifier.lower(),
                )
            )
        ]
        count = len(users_list)
        if count == 1:
            return users_list[0]
        elif count > 1:
            raise KeystoneExceptionError(
                "More than one user with same name exists"
            )

        return None

    def get_role_object(
        self, identifier: str, domain: Optional[Union[Domain, str]] = None
    ) -> Optional[Role]:
        """Returns role object from name or id.

        Look for role in role list and return role object.
        Returns None if role does not exist.

        :param identifier: Role name or id
        :type identifier: str
        :param domain: Domain object, name or id
        :type name: Domain | str | None
        :rtype: Role | None
        """
        if identifier is None:
            return None

        if not isinstance(domain, Domain):
            domain = self.get_domain_object(domain)

        roles = self.api.roles.list(domain=domain)
        logger.debug(f"Roles list in domain {domain}: {roles}")
        if roles is None:
            return None
        for role in roles:
            if (
                role.id == identifier
                or role.name.lower() == identifier.lower()
            ):
                return role

        return None

    def create_service(
        self,
        name: str,
        service_type: str,
        description: str,
        owner: Optional[str] = None,
        may_exist: bool = True,
    ) -> Service:
        """Create service in Keystone.

        :param name: Service name
        :type name: str
        :param service_type: Service type
        :type service_type: str
        :param description: Service description
        :type description: str
        :param owner: Owner of the service
        :type owner: str
        :param may_exist: Check if domain exists or not and return domain if
                          exists
        :type may_exist: bool
        :rtype: Service
        """
        if may_exist:
            services = self.api.services.list(name=name, type=service_type)
            # TODO(wolsen) can we have more than one service with the same
            #  service name? I don't think so, so we'll just handle the first
            #  one for now.
            logger.debug(f"FOUND: {services}")
            for service in services:
                logger.debug(
                    f"Service {name} already exists with "
                    f"service id {service.id}."
                )
                return service

        service = self.api.services.create(
            name=name, type=service_type, description=description
        )
        logger.debug(f"Created service {service.name} with id {service.id}")
        return service

    def create_endpoint(
        self,
        service: Service,
        url: str,
        interface: str,
        region: str,
        may_exist: bool = True,
    ) -> Endpoint:
        """Create endpoint in keystone."""
        ep_string = (
            f"{interface} endpoint for service {service} in "
            f"region {region}"
        )
        if may_exist:
            endpoints = self.api.endpoints.list(
                service=service, interface=interface, region=region
            )
            if endpoints:
                # NOTE(wolsen) if we have endpoints found, there should be only
                # one endpoint; but assert it to make sure
                assert len(endpoints) == 1
                endpoint = endpoints[0]
                if endpoint.url != url:
                    logger.debug(
                        f"{ep_string} ({endpoint.url}) does "
                        f"not match requested url ({url}). Updating."
                    )
                    endpoint = self.api.endpoints.update(
                        endpoint=endpoint, url=url
                    )
                    logger.debug(f"Endpoint updated to use {url}")
                else:
                    logger.debug(
                        f"Endpoint {ep_string} already exists with "
                        f"id {endpoint.id}"
                    )
                return endpoint

        endpoint = self.api.endpoints.create(
            service=service, url=url, interface=interface, region=region
        )
        logger.debug(f"Created endpoint {ep_string} with id {endpoint.id}")
        return endpoint

    def list_endpoint(
        self,
        name: Optional[str] = None,
        interface: Optional[str] = None,
        region: Optional[str] = None,
    ) -> list:
        """List endpoints.

        Returns all the endpoints by default.
        If name is specified, returns the corresponding endpoints.
        If interface is specified, returns the corresponding endpoints.
        If region is specified, returns the corresponding endpoints.
        Response is in the format
        [
            {
                "id": <>,
                "service_id": <>,
                "interface": <>,
                "region": <>,
                "url": <>,
                "enabled": <>,
            }
            ...
        ]

        :param name: Endpoint name name
        :param type: str | None
        :param interface: Endpoint interface
        :param type: str | None
        :param region: Endpoint region
        :param type: str | None
        :rtype: list
        """
        options = {
            "interface": interface,
            "region": region,
        }
        if name is not None:
            services = self.api.services.list(name=name)
            if len(services) != 1:
                return []
            options["service"] = services[0]
        endpoints = self.api.endpoints.list(**options)
        if endpoints is None:
            return []

        endpoint_list = [
            self._convert_endpoint_to_dict(endpoint) for endpoint in endpoints
        ]

        logger.debug(f"Endpoint list: {endpoint_list}")
        return endpoint_list

    # Operations exposed via identity-ops relation

    def list_domain(self, name: Optional[str] = None) -> list:
        """List domains.

        Returns all the domains by default.
        If name is specified, returns the corresponding domain.
        Response is in the format
        [
            {
                "id": <>,
                "name": <>,
                "description": <>,
                "enabled": <>,
            },
            ...
        ]

        :param name: Domain name
        :param type: str | None
        :rtype: list
        """
        domains = self.api.domains.list()
        domains_list = []

        if name:
            domains_list = [
                self._convert_domain_to_dict(domain)
                for domain in domains
                if domain.name.lower() == name.lower()
            ]
        else:
            domains_list = [
                self._convert_domain_to_dict(domain) for domain in domains
            ]

        logger.debug(f"Domain list: {domains_list}")
        return domains_list

    def list_regions(self) -> list:
        """List domains.

        Returns all the regions.
        [
            {
                "id": <>,
                "description": <>,
                "parent_region_id": <>,
            },
            ...
        ]

        :rtype: list
        """
        regions = self.api.regions.list()
        region_list = [
            self._convert_region_to_dict(region) for region in regions
        ]

        logger.debug(f"Region list: {region_list}")
        return region_list

    def show_domain(self, name: str) -> dict:
        """Show domain information.

        Response is in the format
        {
            "id": <>,
            "name": <>,
            "description": <>,
            "enabled": <>,
        },

        :param name: Domain name
        :param type: str
        :rtype: dict
        """
        domains = self.list_domain(name)
        if len(domains) == 1:
            logger.debug(f"Domain for name {name}: {domains[0]}")
            return domains[0]

        return None

    def create_domain(
        self,
        name: str,
        description: str = "Created by Juju",
        enable: bool = True,
        may_exist: bool = True,
    ) -> dict:
        """Create a domain.

        By default, checks if domain already exists with same name
        and returns the domain.
        Response is in the format
        {
            "id": <>,
            "name": <>,
            "description": <>,
            "enabled": <>,
        }

        :param name: Domain name
        :type name: str
        :param description: Description of domain
        :type name: str
        :param enable: Enable or disable domain
        :type enable: bool
        :param may_exist: Check if domain exists or not and return domain if
                          exists
        :type may_exist: bool
        :rtype: dict
        :raises: keystoneauth1.exceptions.http.Conflict
        """
        logger.debug(f"CLIENT: create_domain: name {name}")
        if may_exist:
            domain = self.get_domain_object(name)
            if domain:
                logger.debug(
                    f"Domain {name} already exists with id {domain.id}"
                )
                return self._convert_domain_to_dict(domain)

        domain = self.api.domains.create(
            name=name, description=description, enabled=enable
        )
        logger.debug(f"Created domain {name} with id {domain.id}")
        return self._convert_domain_to_dict(domain)

    def update_domain(
        self,
        domain: str,
        name: Optional[str] = None,
        description: str = "Created by Juju",
        enable: Optional[bool] = None,
    ) -> dict:
        """Update a domain.

        Returns the domain after update.
        Response is in the format
        {
            "id": <>,
            "name": <>,
            "description": <>,
            "enabled": <>,
        }

        :param domain: Existing domain name
        :type domain: str
        :param name: New Domain name
        :type name: str
        :param description: Description of domain
        :type name: str
        :param enable: Enable or disable domain
        :type enable: bool
        :rtype: dict
        :raises: KeystoneExceptionError
        """
        logger.debug(f"CLIENT: update_domain: domain {domain}, name {name}")
        domain_object = self.get_domain_object(domain)
        if not domain_object:
            raise KeystoneExceptionError(f"Domain {domain} does not exist")

        updated_domain = self.api.domains.update(
            domain_object, name=name, description=description, enabled=enable
        )
        logger.debug(f"Updated domain {updated_domain}")
        return self._convert_domain_to_dict(updated_domain)

    def delete_domain(self, name: str) -> None:
        """Delete domain.

        :param name: Domain name
        :type name: str
        """
        self.api.domains.delete(domain=name)
        logger.debug(f"Deleted domain {name}")

    def list_project(self, domain: Optional[str] = None) -> list:
        """List projects in domain.

        If domain is not specified, list all the projects.
        Response is in the format
        [
            {
                "id": <>,
                "name": <>,
                "domain_id": <>,
                "description": <>,
                "enabled": <>,
                "is_domain": <>,
            },
            ...
        ]

        :param domain: Domain name
        :param type: str
        :rtype: list
        """
        domain = self.get_domain_object(domain)
        projects = self.api.projects.list(domain=domain)
        project_list = [
            self._convert_project_to_dict(project) for project in projects
        ]

        logger.debug(f"Project list for domain {domain}: {project_list}")
        return project_list

    def show_project(self, name: str, domain: Optional[str] = None) -> dict:
        """Show project details.

        Response is in the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
            "enabled": <>,
            "is_domain": <>,
        }

        :param name: Project name
        :param type: str
        :param domain: Domain name
        :param type: str
        :rtype: dict
        """
        project = self.get_project_object(name, domain)
        if project:
            logger.debug(f"Project {name} on domain {domain}: {project}")
            return self._convert_project_to_dict(project)

        return None

    def create_project(
        self,
        name: str,
        domain: Optional[str] = None,
        description: str = "Created by Juju",
        enable: bool = True,
        may_exist: bool = True,
    ) -> dict:
        """Create a project.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
            "enabled": <>,
            "is_domain": <>,
        }

        :param name: Project name
        :type name: str
        :param domain: Domain name
        :type domain: str
        :param description: Description of project
        :type name: str
        :param enable: Enable or disable project
        :type enable: bool
        :param may_exist: Check if project exists or not and return project if
                          exists
        :type may_exist: bool
        :rtype: dict
        :raises: keystoneauth1.exceptions.http.Conflict
        """
        logger.debug(f"CLIENT: create_project: name {name}, domain: {domain}")
        if may_exist:
            project = self.get_project_object(name, domain)
            if project:
                logger.debug(
                    f"Project {name} already exists with id {project.id}"
                )
                return self._convert_project_to_dict(project)

        domain = self.get_domain_object(domain)
        project = self.api.projects.create(
            name=name, description=description, domain=domain
        )
        logger.debug(f"Created project {name} with id {project.id}")
        return self._convert_project_to_dict(project)

    def update_project(
        self,
        project: str,
        name: Optional[str] = None,
        domain: Optional[str] = None,
        description: str = "Created by Juju",
        enable: Optional[bool] = None,
    ) -> dict:
        """Update a project.

        Returns the project after update.
        Response is in the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
            "enabled": <>,
            "is_domain": <>,
        }

        :param project: Existing project name
        :type project: str
        :param name: New project name
        :type name: str
        :param domain: Domain name
        :param type: str
        :param description: Description of domain
        :type name: str
        :param enable: Enable or disable domain
        :type enable: bool
        :rtype: dict
        :raises: KeystoneExceptionError
        """
        project_object = self.get_project_object(project, domain)
        if not project_object:
            raise KeystoneExceptionError(f"Project {project} does not exist")

        domain_object = self.get_domain_object(domain)
        updated_project = self.api.projects.update(
            project_object,
            name=name,
            domain=domain_object,
            description=description,
            enabled=enable,
        )
        logger.debug(f"Updated project {updated_project}")
        return self._convert_project_to_dict(updated_project)

    def delete_project(self, name: str, domain: Optional[str] = None) -> None:
        """Delete project.

        :param name: Project name
        :type name: str
        :param domain: Domain name
        :type name: str
        :raises: KeystoneExceptionError
        """
        project_object = self.get_project_object(name, domain)
        if not project_object:
            raise KeystoneExceptionError(f"Project {name} does not exist")

        self.api.projects.delete(project_object)
        logger.debug(f"Deleted project {name} with id {project_object.id}")

    def list_user(
        self,
        domain: Optional[str] = None,
        project: Optional[str] = None,
    ) -> list:
        """List users.

        If domain, group, project are not specified, list all the users.
        Response is in the format
        [
            {
                "id": <>,
                "name": <>,
                "domain_id": <>,
                "enabled": <>,
                "password_expires_at": <>,
            },
            ...
        ]

        :param domain: Domain name
        :param type: str
        :param project: Project name
        :param type: str
        :rtype: list
        """
        domain = self.get_domain_object(domain)
        project = self.get_project_object(project, domain)
        users = self.api.users.list(domain=domain, default_project=project)
        users_list = [self._convert_user_to_dict(project) for user in users]

        logger.debug(
            f"Users list in domain {domain} project {project}: {users_list}"
        )
        return users_list

    def show_user(
        self,
        name: str,
        domain: Optional[str] = None,
        project: Optional[str] = None,
        project_domain: Optional[str] = None,
    ) -> dict:
        """Show user information.

        Response is in the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "enabled": <>,
            "password_expires_at": <>,
        }

        :param name: User name
        :param type: str
        :param domain: Domain name
        :param type: str
        :param project: Project name
        :param type: str
        :param project_domain: Project Domain name
        :param type: str
        :rtype: dict
        """
        try:
            project = self.get_project_object(project, domain)
        except KeystoneExceptionError as e:
            logger.debug(f"Exception in getting project object: {str(e)}")
            if "More than one project with same name" in str(e):
                project = self.get_project_object(project, project_domain)

        user = self.get_user_object(name, domain=domain, project=project)
        if user:
            logger.debug(f"User {name} details: {user}")
            return self._convert_user_to_dict(user)

        return None

    def create_user(
        self,
        name: str,
        password: str,
        email: Optional[str] = None,
        description: Optional[str] = None,
        domain: Optional[str] = None,
        project: Optional[str] = None,
        project_domain: Optional[str] = None,
        enable: bool = True,
        may_exist: bool = True,
    ) -> dict:
        """Create a user.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "enabled": <>,
            "password_expires_at": <>,
        }

        :param name: User name
        :type name: str
        :param password: Password
        :type name: str
        :param email: Email of user
        :param type: str
        :param description: Description of user
        :type name: str
        :param domain: Domain name
        :type domain: str
        :param project: Project name
        :type project: str
        :param project_domain: Project domain name
        :type project_domaindomain: str
        :param enable: Enable or disable user
        :type enable: bool
        :param may_exist: Check if user exists or not and return user if exists
        :type may_exist: bool
        :rtype: dict
        :raises: keystoneauth1.exceptions.http.Conflict, KeystoneExceptionError
        """
        if may_exist:
            try:
                project = self.get_project_object(project, domain)
            except KeystoneExceptionError as e:
                logger.debug(f"Exception in getting project object: {str(e)}")
                if "More than one project with same name" in str(e):
                    project = self.get_project_object(project, project_domain)

            user = self.get_user_object(name, domain=domain, project=project)
            if user:
                logger.debug(f"User {name} already exists with id {user.id}.")
                return self._convert_user_to_dict(user)

        domain = self.get_domain_object(domain)
        user = self.api.users.create(
            name=name,
            domain=domain,
            password=password,
            email=email,
            description=description,
            enabled=enable,
            default_project=project,
        )
        logger.debug(f"Created user {name} with id {user.id}")
        return self._convert_user_to_dict(user)

    def update_user(
        self,
        user: str,
        name: Optional[str] = None,
        password: Optional[str] = None,
        email: Optional[str] = None,
        description: Optional[str] = None,
        domain: Optional[str] = None,
        project: Optional[str] = None,
        project_domain: Optional[str] = None,
        enable: bool = True,
    ) -> dict:
        """Update a user.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "enabled": <>,
            "password_expires_at": <>,
        }

        :param user: User name
        :param type: str
        :param name: New user name
        :type name: str
        :param password: Password
        :type name: str
        :param email: Email of user
        :param type: str
        :param description: Description of user
        :type name: str
        :param domain: Domain name
        :type domain: str
        :param project: Project name
        :type project: str
        :param project_domain: Project domain name
        :type project_domaindomain: str
        :param enable: Enable or disable user
        :type enable: bool
        :param may_exist: Check if user exists or not and return user if exists
        :type may_exist: bool
        :rtype: dict
        :raises: keystoneauth1.exceptions.http.Conflict, KeystoneExceptionError
        """
        try:
            project = self.get_project_object(name, domain)
        except KeystoneExceptionError as e:
            logger.debug(f"Exception in getting project object: {str(e)}")
            if "More than one project with same name" in str(e):
                project = self.get_project_object(name, project_domain)

        user_object = self.get_user_object(
            name, domain=domain, project=project
        )
        if not user_object:
            raise KeystoneExceptionError(f"User {user} does not exist")

        domain_object = self.get_domain_object(domain)
        updated_user = self.api.users.update(
            user_object,
            name=name,
            domain=domain_object,
            password=password,
            email=email,
            description=description,
            enabled=enable,
            default_project=project,
        )
        logger.debug(f"Updated user {user}")
        return self._convert_user_to_dict(updated_user)

    def delete_user(
        self,
        name: str,
        domain: Optional[str] = None,
    ) -> dict:
        """Delete a user using name.

        :param name: User name
        :param type: str
        :param domain: Domain name
        :param type: str | None
        :rtype: dict
        """
        user = self.get_user_object(name, domain=domain)
        self.api.users.delete(user)
        logger.debug("Deleted user {user}")
        # Return deleted users name
        return {"name": name}

    def list_role(self, domain: Optional[str] = None) -> list:
        """List roles in domain.

        If domain is not specified, list all the roles.
        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
        }

        :param domain: Domain name
        :param type: str
        :rtype: list
        """
        domain_object = self.get_domain_object(domain)
        roles = self.api.roles.list(domain=domain_object)
        role_list = [self._convert_role_to_dict(role) for role in roles]
        logger.debug(f"Roles list: {role_list}")
        return role_list

    def create_role(
        self,
        name: str,
        domain: Optional[str] = None,
        may_exist: bool = True,
    ) -> dict:
        """Create a role.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
        }

        :param name: Role name
        :type name: str
        :param domain: Domain name
        :param type: str
        :rtype: dict
        :param may_exist: Check if role exists or not and return role if exists
        :type may_exist: bool
        """
        logger.debug(f"CLIENT: create_role: name {name}, domain {domain}")
        if may_exist:
            role = self.get_role_object(name, domain=domain)
            if role:
                logger.debug(f"Role {name} already exists with id {role.id}")
                return self._convert_role_to_dict(role)

        domain_object = self.get_domain_object(domain)
        role = self.api.roles.create(name=name, domain=domain_object)
        logger.debug(f"Created role {name} with id {role.id}.")
        return self._convert_role_to_dict(role)

    def update_role(
        self,
        role: str,
        name: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> dict:
        """Create a role.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
        }

        :param role: Role name
        :type role: str
        :param name: New role name
        :type name: str
        :param domain: Domain name
        :param type: str
        :rtype: dict
        """
        logger.debug(f"CLIENT: update_role: name {name} domain {domain}")
        role_object = self.get_role_object(name, domain=domain)
        if not role_object:
            raise KeystoneExceptionError(f"Role {role} does not exist")

        updated_role = self.api.roles.update(role_object, name=name)
        logger.debug(f"Updated role {updated_role}")
        return self._convert_role_to_dict(updated_role)

    def delete_role(
        self,
        name: str,
        domain: Optional[str] = None,
    ) -> None:
        """Delete a role using name.

        :param name: Role name
        :param type: str
        :param domain: Domain name
        :param type: str | None
        """
        role = self.get_role_object(name, domain=domain)
        self.api.roles.delete(role)
        logger.debug(f"Deleted role {name}")

    def grant_role(
        self,
        role: str,
        domain: Optional[str] = None,
        project: Optional[str] = None,
        user: Optional[str] = None,
        project_domain: Optional[str] = None,
        user_domain: Optional[str] = None,
        role_domain: Optional[str] = None,
    ) -> None:
        """Grant role to a user.

        Response is of the format
        {
            "id": <>,
            "name": <>,
            "domain_id": <>,
            "description": <>,
        }

        :param role: Role name
        :param type: str
        :param domain: Domain name
        :param type: str
        :param project: Project name
        :param type: str
        :param user: User name
        :param type: str
        :param project_domain: Project domain name
        :param type: str
        :param user_domain: User domain name
        :param type: str
        :param role_domain: Role domain name
        :param type: str
        :raises: ValueError, KeystoneExceptionError
        """
        if project and domain:
            raise ValueError("Project and domain are mutually exclusive")
        if not project and not domain:
            raise ValueError("Project or domain must be specified")

        role_object = self.get_role_object(role, domain=role_domain)
        user_object = self.get_user_object(user, domain=user_domain)
        domain_object = self.get_domain_object(domain)
        project_object = self.get_project_object(
            project, domain=project_domain
        )
        self.api.roles.grant(
            role=role_object,
            user=user_object,
            domain=domain_object,
            project=project_object,
        )
        logger.debug(
            f"Granted role {role_object} to user {user_object} in domain "
            f"{domain_object}/project {project_object}"
        )
