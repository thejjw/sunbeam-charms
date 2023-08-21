#!/usr/bin/env python3
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

"""Heat Operator Charm.

This charm provide heat services as part of an OpenStack deployment
"""

import hashlib
import json
import logging
import secrets
import string
from typing import (
    List,
    Mapping,
)

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import pwgen
from charms.keystone_k8s.v0.identity_resource import (
    IdentityOpsProviderGoneAwayEvent,
    IdentityOpsProviderReadyEvent,
    IdentityOpsResponseEvent,
)
from ops.charm import (
    RelationEvent,
    SecretChangedEvent,
    SecretRemoveEvent,
    SecretRotateEvent,
)
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)
from ops.model import (
    ModelError,
    Relation,
    SecretNotFoundError,
    SecretRotate,
)

logger = logging.getLogger(__name__)

CREDENTIALS_SECRET_PREFIX = "credentials_"
HEAT_API_CONTAINER = "heat-api"
HEAT_ENGINE_CONTAINER = "heat-engine"
HEAT_API_SERVICE_KEY = "api-service"


class HeatAPIPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Heat API container."""

    def get_layer(self):
        """Heat API service.

        :returns: pebble service layer configuration for heat api service
        :rtype: dict
        """
        if self.charm.service_name == "heat-api-cfn":
            return {
                "summary": "heat api cfn layer",
                "description": "pebble configuration for heat api cfn service",
                "services": {
                    "heat-api": {
                        "override": "replace",
                        "summary": "Heat API CFN",
                        "command": "heat-api-cfn",
                        "startup": "enabled",
                        "user": "heat",
                        "group": "heat",
                    }
                },
            }
        else:
            return {
                "summary": "heat api layer",
                "description": "pebble configuration for heat api service",
                "services": {
                    "heat-api": {
                        "override": "replace",
                        "summary": "Heat API",
                        "command": "heat-api",
                        "startup": "enabled",
                        "user": "heat",
                        "group": "heat",
                    }
                },
            }

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for heat service
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "http": {
                        "url": f"{self.charm.healthcheck_http_url}/healthcheck"
                    },
                },
            }
        }


class HeatEnginePebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Heat engine container."""

    def get_layer(self):
        """Heat Engine service.

        :returns: pebble service layer configuration for heat engine service
        :rtype: dict
        """
        return {
            "summary": "heat engine layer",
            "description": "pebble configuration for heat engine service",
            "services": {
                "heat-engine": {
                    "override": "replace",
                    "summary": "Heat Engine",
                    "command": "heat-engine",
                    "startup": "enabled",
                    "user": "heat",
                    "group": "heat",
                }
            },
        }


class HeatConfigurationContext(sunbeam_config_contexts.ConfigContext):
    """Heat configuration context."""

    def context(self) -> dict:
        """Heat configuration context."""
        (
            username,
            password,
        ) = self.charm.get_stack_admin_credentials_to_configure()
        return {
            "stack_domain_name": self.charm.stack_domain_name,
            "stack_domain_admin_user": username,
            "stack_domain_admin_password": password,
        }


class HeatOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    wsgi_admin_script = "/usr/bin/heat-wsgi-api"
    wsgi_public_script = "/usr/bin/heat-wsgi-api"
    heat_auth_encryption_key = "auth-encryption-key"

    db_sync_cmds = [["heat-manage", "db_sync"]]

    mandatory_relations = {
        "database",
        "amqp",
        "identity-service",
        "ingress-public",
        "identity-ops",
    }

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(identity_ops_ready=False)

    def hash_ops(self, ops: list) -> str:
        """Return the sha1 of the requested ops."""
        return hashlib.sha1(json.dumps(ops).encode()).hexdigest()

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.id_ops = sunbeam_rhandlers.IdentityResourceRequiresHandler(
            self,
            "identity-ops",
            self.handle_keystone_ops,
            mandatory="identity-ops" in self.mandatory_relations,
        )
        handlers.append(self.id_ops)
        return handlers

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = [
            HeatAPIPebbleHandler(
                self,
                HEAT_API_CONTAINER,
                "heat-api",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            HeatEnginePebbleHandler(
                self,
                HEAT_ENGINE_CONTAINER,
                "heat-engine",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_heat_auth_encryption_key(self):
        """Return the shared metadata secret."""
        return self.leader_get(self.heat_auth_encryption_key)

    def set_heat_auth_encryption_key(self):
        """Store the shared metadata secret."""
        alphabet = string.ascii_letters + string.digits
        key = "".join(secrets.choice(alphabet) for i in range(32))
        self.leader_set({self.heat_auth_encryption_key: key})

    def configure_charm(self, event):
        """Configure charm.

        Ensure setting the auth key is first as services in container need it
        to start.
        """
        if self.unit.is_leader():
            auth_key = self.get_heat_auth_encryption_key()
            if auth_key:
                logger.debug("Found auth key in leader DB")
            else:
                logger.debug("Creating auth key")
                self.set_heat_auth_encryption_key()

        super().configure_charm(event)

    def configure_app_leader(self, event):
        """Configure app leader.

        Ensure setting service_name in peer relation application data if it
        does not exist.
        """
        super().configure_app_leader(event)

        # Update service name in application data
        if not self.peers.get_app_data(HEAT_API_SERVICE_KEY):
            self.peers.set_app_data({HEAT_API_SERVICE_KEY: self.service_name})

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Set database name as heat for both heat-api, heat-api-cfn.
        """
        return {
            "database": "heat",
        }

    @property
    def service_name(self) -> str:
        """Update service_name to heat-api or heat-api-cfn.

        service_name should be updated only once. Get service name from app data if
        it exists and ignore the charm configuration parameter api-service.
        If app data does not exist, return with the value from charm configuration.
        """
        service_name = None
        if hasattr(self, "peers"):
            service_name = self.peers.get_app_data(HEAT_API_SERVICE_KEY)

        if not service_name:
            service_name = self.config.get("api_service")
            if service_name not in ["heat-api", "heat-api-cfn"]:
                logger.warning(
                    "Config parameter api_service should be one of heat-api, heat-api-cfn, defaulting to heat-api."
                )
                service_name = "heat-api"

        return service_name

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/heat/heat.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "heat"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "heat"

    @property
    def service_endpoints(self):
        """Return heat service endpoints."""
        if self.service_name == "heat-api-cfn":
            return [
                {
                    "service_name": "heat-cfn",
                    "type": "cloudformation",
                    "description": "OpenStack Heat CloudFormation API",
                    "internal_url": f"{self.internal_url}/v1/$(tenant_id)s",
                    "public_url": f"{self.public_url}/v1/$(tenant_id)s",
                    "admin_url": f"{self.admin_url}/v1/$(tenant_id)s",
                }
            ]
        else:
            return [
                {
                    "service_name": "heat",
                    "type": "orchestration",
                    "description": "OpenStack Heat API",
                    "internal_url": f"{self.internal_url}/v1/$(tenant_id)s",
                    "public_url": f"{self.public_url}/v1/$(tenant_id)s",
                    "admin_url": f"{self.admin_url}/v1/$(tenant_id)s",
                },
            ]

    @property
    def default_public_ingress_port(self):
        """Port for Heat API service."""
        # Port 8000 if api service is heat-api-cfn
        if self.service_name == "heat-api-cfn":
            return 8000

        # Default heat-api port
        return 8004

    @property
    def wsgi_container_name(self) -> str:
        """Name of the WSGI application container."""
        # Container name for both heat-api and heat-api-cfn service is heat-api
        return "heat-api"

    @property
    def stack_domain_name(self) -> str:
        """Domain name for heat template-defined users."""
        return "heat"

    @property
    def stack_domain_admin_user(self) -> str:
        """User to manage users and projects in stack_domain_name."""
        if self.service_name == "heat-api-cfn":
            return "heat_domain_admin_cfn"
        else:
            return "heat_domain_admin"

    @property
    def stack_user_role(self) -> str:
        """Role for heat template-defined users."""
        return "heat_stack_user"

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend([HeatConfigurationContext(self, "heat")])
        return _cadapters

    def default_container_configs(self):
        """Return base container configs."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/heat.conf", "root", "heat"
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/api-paste.ini", "root", "heat"
            ),
        ]

    def _get_stack_admin_credentials_to_ops_secret(self) -> str:
        """Get stack domain admin secret sent to ops."""
        label = f"{CREDENTIALS_SECRET_PREFIX}{self.stack_domain_admin_user}"
        credentials_id = self.peers.get_app_data(label)

        if not credentials_id:
            credentials_id = self._retrieve_or_set_secret(
                self.stack_domain_admin_user,
                rotate=SecretRotate.MONTHLY,
                add_suffix_to_username=True,
            )

        return credentials_id

    def _grant_stack_admin_credentials_to_ops_secret(
        self, relation: Relation
    ) -> None:
        """Grant secret access to the related units."""
        try:
            credentials_id = self._get_stack_admin_credentials_to_ops_secret()
            secret = self.model.get_secret(id=credentials_id)
            logger.debug(
                f"Granting access to secret {credentials_id} for relation "
                f"{relation.app.name} {relation.name}/{relation.id}"
            )
            secret.grant(relation)
        except (ModelError, SecretNotFoundError) as e:
            logger.debug(
                f"Error during granting access to secret {credentials_id} for "
                f"relation {relation.app.name} {relation.name}/{relation.id}: "
                f"{str(e)}"
            )

    # 2 secrets maintained for stack admin user credentials. One is used to
    # sent to identity-ops to create user in keystone. Once the user is created
    # in keystone, this charm identifies via the identity-ops available
    # response event and updates 2nd secret. The 2nd secret is used to
    # template the heat configuration.
    # This separation is mainly required to handle user rotation every
    # month in an automatic fashion.
    def get_stack_admin_credentials_to_ops(self) -> tuple:
        """Credentials for stack domain admin user to be sent to ops."""
        credentials_id = self._get_stack_admin_credentials_to_ops_secret()
        credentials = self.model.get_secret(id=credentials_id)
        username = credentials.get_content().get("username")
        user_password = credentials.get_content().get("password")
        return (username, user_password)

    def set_stack_admin_credentials_to_configure(self) -> bool:
        """Set domain admin credentials to configure in heat conf."""
        label = (
            f"{CREDENTIALS_SECRET_PREFIX}configure_"
            f"{self.stack_domain_admin_user}"
        )
        try:
            credentials_id = self.peers.get_app_data(label)
            username, password = self.get_stack_admin_credentials_to_ops()
            if credentials_id:
                credentials = self.model.get_secret(id=credentials_id)
                credentials.set_content(
                    {"username": username, "password": password}
                )
            else:
                credentials_secret = self.model.app.add_secret(
                    {"username": username, "password": password},
                    label=label,
                )
                self.peers.set_app_data({label: credentials_secret.id})
        except (ModelError, SecretNotFoundError) as e:
            logger.debug(str(e))
            return False

        return True

    def get_stack_admin_credentials_to_configure(self) -> tuple:
        """Get domain admin credentials to configiure in heat conf."""
        label = (
            f"{CREDENTIALS_SECRET_PREFIX}configure_"
            f"{self.stack_domain_admin_user}"
        )
        try:
            credentials_id = self.peers.get_app_data(label)
            if credentials_id:
                credentials = self.model.get_secret(id=credentials_id)
                credentials = credentials.get_content()
                username = credentials.get("username")
                password = credentials.get("password")
                return (username, password)
        except (ModelError, SecretNotFoundError) as e:
            logger.debug(str(e))

        return None, None

    def add_domain_admin_to_users_list_to_delete(
        self, old_stack_user: str
    ) -> None:
        """Update users list to delete."""
        logger.debug(f"Adding stack user to delete list {old_stack_user}")
        old_stack_users = self.peers.get_app_data("old_stack_users")
        stack_users_to_delete = (
            json.loads(old_stack_users) if old_stack_users else []
        )
        if old_stack_user not in stack_users_to_delete:
            stack_users_to_delete.append(old_stack_user)
            self.peers.set_app_data(
                {"old_stack_users": json.dumps(stack_users_to_delete)}
            )

    def _retrieve_or_set_secret(
        self,
        username: str,
        rotate: SecretRotate = SecretRotate.NEVER,
        add_suffix_to_username: bool = False,
    ) -> str:
        """Retrieve or create a secret."""
        label = f"{CREDENTIALS_SECRET_PREFIX}{username}"
        credentials_id = self.peers.get_app_data(label)
        if credentials_id:
            return credentials_id

        password = pwgen.pwgen(12)
        if add_suffix_to_username:
            suffix = pwgen.pwgen(6)
            username = f"{username}-{suffix}"
        credentials_secret = self.model.app.add_secret(
            {"username": username, "password": password},
            label=label,
            rotate=rotate,
        )
        self.peers.set_app_data(
            {
                label: credentials_secret.id,
            }
        )
        return credentials_secret.id

    def _get_heat_stack_domain_ops(self) -> list:
        """Generate ops request for domain setup."""
        credentials_id = self._get_stack_admin_credentials_to_ops_secret()
        username, _ = self.get_stack_admin_credentials_to_ops()
        ops = [
            # Create domain heat
            {
                "name": "create_domain",
                "params": {"name": "heat", "enable": True},
            },
            # Create role heat_stack_user
            {"name": "create_role", "params": {"name": "heat_stack_user"}},
            # Create user heat_domain_admin
            {
                "name": "create_user",
                "params": {
                    "name": username,
                    "password": credentials_id,
                    "domain": "heat",
                },
            },
            # Grant role admin to heat_domain_admin user
            {
                "name": "grant_role",
                "params": {
                    "role": "admin",
                    "domain": "heat",
                    "user": username,
                    "user_domain": "heat",
                },
            },
        ]
        return ops

    def _delete_stack_users(self, users: list) -> list:
        """Generate ops to delete stack users."""
        ops = []
        for user in users:
            ops.append(
                {
                    "name": "delete_user",
                    "params": {"name": user, "domain": "heat"},
                }
            )

        return ops

    def _handle_initial_heat_domain_setup_response(
        self, event: RelationEvent
    ) -> None:
        """Handle domain setup response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get("ops", [])
        } == {0}:
            logger.debug(
                "Initial heat domain setup commands completed, running "
                "configure charm"
            )
            (
                username,
                password,
            ) = self.get_stack_admin_credentials_to_configure()
            if self.set_stack_admin_credentials_to_configure():
                if username:
                    self.add_domain_admin_to_users_list_to_delete(username)
                self.configure_charm(event)
        else:
            logger.debug(
                "Error in running initial domain setup ops "
                f"{self.id_ops.interface.response}"
            )

    def _handle_create_stack_user_response(self, event: RelationEvent) -> None:
        """Handle create stack user response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get("ops", [])
        } == {0}:
            logger.debug(
                "Create stack user completed, running configure charm"
            )
            (
                username,
                password,
            ) = self.get_stack_admin_credentials_to_configure()
            if self.set_stack_admin_credentials_to_configure():
                if username:
                    self.add_domain_admin_to_users_list_to_delete(username)
        else:
            logger.debug(
                "Error in creation of stack user ops "
                f"{self.id_ops.interface.response}"
            )

    def _handle_delete_stack_users_response(
        self, event: RelationEvent
    ) -> None:
        """Handle delete stack user response from identity-ops."""
        deleted_users = []
        not_deleted_users = []
        for op in self.id_ops.interface.response.get("ops", []):
            if op.get("return-code") == 0:
                deleted_users.append(op.get("value").get("name"))
            else:
                not_deleted_users.append(op.get("value").get("name"))
        logger.debug(
            f"Deleted users: {deleted_users}, not_deleted_users: "
            f"{not_deleted_users}"
        )

        old_stack_users = self.peers.get_app_data("old_stack_users")
        stack_users_to_delete = (
            json.loads(old_stack_users) if old_stack_users else []
        )
        new_stack_users_to_delete = [
            x for x in stack_users_to_delete if x not in deleted_users
        ]
        self.peers.set_app_data(
            {"old_stack_users": json.dumps(new_stack_users_to_delete)}
        )

    def handle_keystone_ops(self, event: RelationEvent) -> None:
        """Event handler for identity ops."""
        if isinstance(event, IdentityOpsProviderReadyEvent):
            self._state.identity_ops_ready = True

            if not self.unit.is_leader():
                return

            # Send op request only by leader unit
            ops = self._get_heat_stack_domain_ops()
            id_ = self.hash_ops(ops)
            self._grant_stack_admin_credentials_to_ops_secret(event.relation)
            request = {
                "id": id_,
                "tag": "initial_heat_domain_setup",
                "ops": ops,
            }
            logger.debug(f"Sending ops request: {request}")
            self.id_ops.interface.request_ops(request)
        elif isinstance(event, IdentityOpsProviderGoneAwayEvent):
            self._state.identity_ops_ready = False
        elif isinstance(event, IdentityOpsResponseEvent):
            if not self.unit.is_leader():
                return

            logger.debug(
                f"Got response from keystone: {self.id_ops.interface.response}"
            )
            request_tag = self.id_ops.interface.response.get("tag")
            if request_tag == "initial_heat_domain_setup":
                self._handle_initial_heat_domain_setup_response(event)
            elif request_tag == "create_stack_user":
                self._handle_create_stack_user_response(event)
            elif request_tag == "delete_stack_users":
                self._handle_delete_stack_users_response(event)
            else:
                logger.debug("Ignore handling response for tag {request_tag}")

    def _on_secret_changed(self, event: SecretChangedEvent):
        logger.debug(
            f"secret-changed triggered for label {event.secret.label}"
        )
        stack_user_label = (
            f"{CREDENTIALS_SECRET_PREFIX}configure_"
            f"{self.stack_domain_admin_user}"
        )

        # Secret change on configured stack admin secret
        if event.secret.label == stack_user_label:
            logger.debug(
                "Calling configure charm to populate heat stack user info in "
                "configuration files"
            )
            self.configure_charm(event)
        else:
            logger.debug(
                "Ignoring the secret-changed event for label "
                f"{event.secret.label}"
            )

    def _on_secret_rotate(self, event: SecretRotateEvent):
        # All the juju secrets are created on leader unit, so return
        # if unit is not leader at this stage instead of checking at
        # each secret.
        logger.debug(f"secret-rotate triggered for label {event.secret.label}")
        if not self.unit.is_leader():
            logger.debug("Not leader unit, no action required")
            return

        stack_user_label = (
            f"{CREDENTIALS_SECRET_PREFIX}{self.stack_domain_admin_user}"
        )
        # Secret rotate on stack admin secret sent to ops
        if event.secret.label == stack_user_label:
            suffix = pwgen.pwgen(6)
            username = f"{self.stack_domain_admin_user}-{suffix}"
            password = pwgen.pwgen(12)
            event.secret.set_content(
                {
                    "username": username,
                    "password": password,
                }
            )

            # Can reuse _get_heat_stack_domain_ops as creation of domain
            # and role are extra steps and identity-ops is idempotent so
            # sending those ops commands does not cause any issues.
            ops = self._get_heat_stack_domain_ops()
            id_ = self.hash_ops(ops)
            request = {
                "id": id_,
                "tag": "create_stack_user",
                "ops": ops,
            }
            logger.debug(f"Sending ops request: {request}")
            self.id_ops.interface.request_ops(request)
        else:
            logger.debug(
                "Ignoring the secret-rotate event for label "
                f"{event.secret.label}"
            )

    def _on_secret_remove(self, event: SecretRemoveEvent):
        logger.debug(f"secret-remove triggered for label {event.secret.label}")
        if not self.unit.is_leader():
            logger.debug("Not leader unit, no action required")
            return

        stack_user_label = (
            f"{CREDENTIALS_SECRET_PREFIX}configure_"
            f"{self.stack_domain_admin_user}"
        )
        # Secret remove on configured stack admin secret
        if event.secret.label == stack_user_label:
            old_stack_users = self.peers.get_app_data("old_stack_users")
            stack_users_to_delete = (
                json.loads(old_stack_users) if old_stack_users else []
            )

            if not stack_users_to_delete:
                return

            # Check if previous request is processed or not??
            # if not, just ignore/defer sending this request
            ops = self._delete_stack_users(stack_users_to_delete)
            id_ = self.hash_ops(ops)
            request = {
                "id": id_,
                "tag": "delete_stack_users",
                "ops": ops,
            }
            logger.debug(f"Sending ops request: {request}")
            self.id_ops.interface.request_ops(request)
        else:
            logger.debug(
                "Ignoring the secret-remove event for label "
                f"{event.secret.label}"
            )


if __name__ == "__main__":
    main(HeatOperatorCharm)
