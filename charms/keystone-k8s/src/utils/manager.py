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

"""Manager for interacting with keystone."""

import logging
from typing import (
    Mapping,
    Optional,
)

import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
from keystoneauth1 import (
    session,
)
from keystoneauth1.identity import (
    v3,
)
from keystoneclient.v3 import (
    client,
)
from ops.model import (
    MaintenanceStatus,
)
from utils.client import (
    KeystoneClient,
    KeystoneExceptionError,
)

logger = logging.getLogger(__name__)
_OIDC_METADATA_FOLDER = "/etc/apache2/oidc-metadata"
_KEYSTONE_COMBINED_CA = (
    "/usr/local/share/ca-certificates/keystone-combined.crt"
)


class KeystoneManager:
    """Class for managing interactions with keystone api."""

    def __init__(
        self,
        charm: sunbeam_charm.OSBaseOperatorCharmK8S,
        container_name: str,
    ):
        """Setup the manager."""
        self.charm = charm
        self.container_name = container_name
        self._api = None
        self._ksclient = None

    def run_cmd(self, cmd, exception_on_error=True, **kwargs):
        """Run command in container."""
        pebble_handler = self.charm.get_named_pebble_handler(
            self.container_name
        )
        return pebble_handler.execute(cmd, exception_on_error, **kwargs)

    @property
    def api(self):
        """Returns the current api reference or creates a new one.

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
            system_scope="all",
            project_domain_name="Default",
            user_domain_name="Default",
        )
        keystone_session = session.Session(auth=auth)
        self._api = client.Client(
            session=keystone_session,
            endpoint_override="http://localhost:5000/v3",
        )
        return self._api

    @property
    def ksclient(self) -> KeystoneClient:
        """Keystone client."""
        if self._ksclient:
            return self._ksclient

        return KeystoneClient(self.api)

    @property
    def admin_endpoint(self):
        """Admin endpoint for this keystone."""
        return self.charm.admin_endpoint

    @property
    def internal_endpoint(self):
        """Internal endpoint for this keystone."""
        return self.charm.internal_endpoint

    @property
    def public_endpoint(self):
        """Public endpoint for this keystone."""
        return self.charm.public_endpoint

    @property
    def regions(self):
        """List of regions required for this keystone."""
        return [self.charm.model.config["region"]]

    def setup_keystone(self):
        """Runs the keystone setup process for first time configuration.

        Runs through the keystone setup process for initial installation and
        configuration. This involves creating the database, setting up fernet
        repositories for tokens and credentials, and bootstrapping the initial
        keystone service.
        """
        with sunbeam_guard.guard(self.charm, "Initializing Keystone", False):
            self._fernet_setup()
            self._credential_setup()
            self._bootstrap()

    def setup_oidc_metadata_folder(self):
        """Create the OIDC metadata folder and set permissions."""
        self.run_cmd(["sudo", "mkdir", "-p", _OIDC_METADATA_FOLDER])
        self.run_cmd(
            ["sudo", "chown", "keystone:www-data", _OIDC_METADATA_FOLDER]
        )
        self.run_cmd(["sudo", "chmod", "550", _OIDC_METADATA_FOLDER])

    def rotate_fernet_keys(self):
        """Rotate the fernet keys.

        See for more information:
        https://docs.openstack.org/keystone/latest/admin/fernet-token-faq.html

        This should be called on the leader unit,
        at intervals of
        (token-expiration + allow-expired-window)/(fernet-max-active-keys - 2)
        """
        with sunbeam_guard.guard(self.charm, "Rotating fernet keys"):
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "fernet_rotate",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ]
            )

    def rotate_credential_keys(self):
        """Rotate the credential keys.

        See for more information:
        https://docs.openstack.org/keystone/latest/admin/credential-encryption.html
        """
        with sunbeam_guard.guard(self.charm, "Rotating credential keys"):
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "credential_migrate",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ]
            )
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "credential_rotate",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ]
            )

    def write_combined_ca(self) -> None:
        """Write the combined CA to the container."""
        ca_contents = self.charm.get_ca_and_chain()
        oauth_ca_certs = self.charm.get_ca_bundles_from_oauth_relations()
        container = self.charm.unit.get_container(self.container_name)
        if not ca_contents and not oauth_ca_certs:
            logger.debug(
                "No CA contents found to write to keystone container."
            )
            # remove the existing CA file if it exists
            try:
                container.remove_path(_KEYSTONE_COMBINED_CA)
            except ops.pebble.PathError:
                logger.debug("No existing CA file to remove.")
            return
        else:
            combined = []
            if ca_contents:
                combined.append(ca_contents)
            if oauth_ca_certs:
                combined.extend(oauth_ca_certs)
            container.push(
                _KEYSTONE_COMBINED_CA,
                "\n".join(combined),
                user="root",
                group="root",
                permissions=0o644,
            )
        self.run_cmd(["sudo", "update-ca-certificates", "--fresh"])

    def write_oidc_metadata(self, metadata: Mapping[str, str]) -> None:
        """Write the OIDC metadata to the container."""
        container = self.charm.unit.get_container(self.container_name)
        for filename, contents in metadata.items():
            container.push(
                f"{_OIDC_METADATA_FOLDER}/{filename}",
                contents,
                user="keystone",
                group="www-data",
                permissions=0o440,
            )

        # remove old metadata files
        files = container.list_files(_OIDC_METADATA_FOLDER)
        for file in files:
            if file.name not in metadata:
                container.remove_path(file.path)

    def read_keys(self, key_repository: str) -> Mapping[str, str]:
        """Pull the fernet keys from the on-disk repository."""
        container = self.charm.unit.get_container(self.container_name)
        files = container.list_files(key_repository)
        # Ignore file type directory. This is to ignore lost+found directory
        return {
            file.name: container.pull(file.path).read()
            for file in files
            if file.type == ops.pebble.FileType.FILE
        }

    def write_keys(self, key_repository: str, keys: Mapping[str, str]) -> None:
        """Update the local fernet key repository with the provided keys."""
        container = self.charm.unit.get_container(self.container_name)

        logger.debug(f"Writing updated fernet keys at {key_repository}")

        # write the keys
        for filename, contents in keys.items():
            container.push(
                f"{key_repository}/{filename}",
                contents,
                user="keystone",
                group="keystone",
                permissions=0o600,
            )

        # remove old keys
        files = container.list_files(key_repository)
        for file in files:
            if file.name not in keys:
                container.remove_path(file.path)

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
        """Syncs the database using the keystone-manage db_sync.

        The database is synchronized using the keystone-manage db_sync command.
        Database configuration information is retrieved from configuration
        files.

        :raises: KeystoneExceptionError when the database sync fails.
        """
        try:
            self._set_status("Syncing database")
            logger.info("Syncing database...")
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "--config-dir",
                    "/etc/keystone",
                    "db_sync",
                ]
            )
        except ops.pebble.ExecError:
            logger.exception("Error occurred synchronizing the database.")
            raise KeystoneExceptionError("Database sync failed")

    def _fernet_setup(self):
        """Sets up the fernet token store in the specified container.

        :raises: KeystoneExceptionError when a failure occurs setting up the fernet
                 token store
        """
        try:
            self._set_status("Setting up fernet tokens")
            logger.info("Setting up fernet tokens...")
            self.run_cmd(
                [
                    "sudo",
                    "chown",
                    "keystone:keystone",
                    "/etc/keystone/fernet-keys",
                ]
            )
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "fernet_setup",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ]
            )
        except ops.pebble.ExecError:
            logger.exception("Error occurred setting up fernet tokens")
            raise KeystoneExceptionError("Fernet setup failed.")

    def _credential_setup(self):
        """Run keystone credential_setup."""
        try:
            self._set_status("Setting up credentials")
            logger.info("Setting up credentials...")
            self.run_cmd(
                [
                    "sudo",
                    "chown",
                    "keystone:keystone",
                    "/etc/keystone/credential-keys",
                ]
            )
            self.run_cmd(
                [
                    "sudo",
                    "-u",
                    "keystone",
                    "keystone-manage",
                    "credential_setup",
                    "--keystone-user",
                    "keystone",
                    "--keystone-group",
                    "keystone",
                ]
            )
        except ops.pebble.ExecError:
            logger.exception("Error occurred during credential setup")
            raise KeystoneExceptionError("Credential setup failed.")

    def _bootstrap(self):
        """Run keystone bootstrap."""
        try:
            self._set_status("Bootstrapping Keystone")
            logger.info("Bootstrapping keystone service")

            # NOTE(wolsen) in classic keystone charm, there's a comment about
            # enabling immutable roles for this. This is unnecessary as it is
            # now the default behavior for keystone-manage bootstrap.
            self.run_cmd(
                [
                    "keystone-manage",
                    "bootstrap",
                    "--bootstrap-username",
                    self.charm.charm_user,
                    "--bootstrap-password",
                    self.charm.charm_password,
                    "--bootstrap-project-name",
                    "admin",
                    "--bootstrap-role-name",
                    self.charm.admin_role,
                    "--bootstrap-service-name",
                    "keystone",
                    "--bootstrap-admin-url",
                    self.admin_endpoint,
                    "--bootstrap-public-url",
                    self.public_endpoint,
                    "--bootstrap-internal-url",
                    self.internal_endpoint,
                    "--bootstrap-region-id",
                    self.regions[0],
                ]
            )
        except ops.pebble.ExecError:
            logger.exception("Error occurred bootstrapping keystone service")
            raise KeystoneExceptionError("Bootstrap failed")

    def setup_initial_projects_and_users(self):
        """Setup initial projects and users."""
        with sunbeam_guard.guard(
            self.charm, "Setting up initial projects and users", False
        ):
            self._setup_admin_accounts()
            self._setup_service_accounts()
            self.update_service_catalog_for_keystone()

    def _setup_admin_accounts(self):
        """Setup admin accounts."""
        # Get the default domain id
        default_domain = self.ksclient.get_domain_object("default")
        logger.debug(f"Default domain id: {default_domain.id}")
        self.charm._state.default_domain_id = default_domain.id  # noqa

        # Get the admin domain id
        admin_domain = self.ksclient.create_domain(name="admin_domain")
        admin_domain_id = admin_domain.get("id")
        logger.debug(f"Admin domain id: {admin_domain_id}")
        self.charm._state.admin_domain_id = admin_domain_id  # noqa
        self.charm._state.admin_domain_name = admin_domain.get("name")  # noqa

        # Ensure that we have the necessary projects: admin and service
        admin_project = self.ksclient.create_project(
            name="admin", domain=self.charm.admin_domain_name
        )

        logger.debug("Ensuring admin user exists")
        self.ksclient.create_user(
            name=self.charm.admin_user,
            password=self.charm.admin_password,
            domain=self.charm.admin_domain_name,
        )

        logger.debug("Ensuring roles exist for admin")
        # I seem to recall all kinds of grief between Member and member and
        # _member_ and inconsistencies in what other projects expect.
        member_role = self.ksclient.create_role(name="member")
        self.ksclient.create_role(name=self.charm.admin_role)

        logger.debug("Granting roles to admin user")
        # Make the admin a member of the admin project
        self.ksclient.grant_role(
            role=member_role.get("name"),
            user=self.charm.admin_user,
            project=admin_project.get("name"),
            project_domain=self.charm.admin_domain_name,
            user_domain=self.charm.admin_domain_name,
        )
        # Make the admin an admin of the admin project
        self.ksclient.grant_role(
            role=self.charm.admin_role,
            user=self.charm.admin_user,
            project=admin_project.get("name"),
            project_domain=self.charm.admin_domain_name,
            user_domain=self.charm.admin_domain_name,
        )
        # Make the admin a domain-level admin
        self.ksclient.grant_role(
            role=self.charm.admin_role,
            user=self.charm.admin_user,
            domain=self.charm.admin_domain_name,
            user_domain=self.charm.admin_domain_name,
        )

    def _setup_service_accounts(self):
        """Create service accounts."""
        # Get the service domain id
        service_domain = self.ksclient.create_domain(
            name="service_domain", may_exist=True
        )
        service_domain_id = service_domain.get("id")
        logger.debug(f"Service domain id: {service_domain_id}.")

        service_project = self.ksclient.create_project(
            name=self.charm.service_project,
            domain=service_domain.get("name"),
        )
        service_project_id = service_project.get("id")
        logger.debug(f"Service project id: {service_project_id}.")
        self.charm._state.service_project_id = service_project_id  # noqa

    def create_service_account(
        self,
        username: str,
        password: str,
        project: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> dict:
        """Helper function to create service account."""
        if not domain:
            domain = "service_domain"
        if not project:
            project = self.charm.service_project

        service_user = self.ksclient.create_user(
            name=username,
            password=password,
            domain=domain,
        )
        # NOTE(gboutry): Remove admin role when services support working with
        # service role only.
        self.ksclient.grant_role(
            role=self.charm.admin_role,
            project=project,
            user=service_user.get("name"),
            project_domain="service_domain",
            user_domain="service_domain",
        )
        # Service role introduced in 2023.2
        self.ksclient.grant_role(
            role="service",
            project=project,
            user=service_user.get("name"),
            project_domain="service_domain",
            user_domain="service_domain",
        )
        return service_user

    def update_service_catalog_for_keystone(self):
        """Create identity service in catalogue."""
        service = self.ksclient.create_service(
            name="keystone",
            service_type="identity",
            description="Keystone Identity Service",
            may_exist=True,
        )

        endpoints = {
            "admin": self.admin_endpoint,
            "internal": self.internal_endpoint,
            "public": self.public_endpoint,
        }

        for region in self.regions:
            if not region:
                continue

            for interface, url in endpoints.items():
                self.ksclient.create_endpoint(
                    service=service,
                    interface=interface,
                    url=url,
                    region=region,
                    may_exist=True,
                )
