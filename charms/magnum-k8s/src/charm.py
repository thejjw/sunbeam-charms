#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Magnum Operator Charm.

This charm provide Magnum services as part of an OpenStack deployment
"""

import hashlib
import json
import logging
from typing import (
    TYPE_CHECKING,
    List,
)

import charms.keystone_k8s.v0.identity_resource as identity_resource
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import pwgen
from ops.charm import (
    RelationEvent,
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
MAGNUM_API_CONTAINER = "magnum-api"
MAGNUM_CONDUCTOR_CONTAINER = "magnum-conductor"


class MagnumConfigurationContext(sunbeam_config_contexts.ConfigContext):
    """Magnum configuration context."""

    if TYPE_CHECKING:
        charm: "MagnumOperatorCharm"

    def context(self) -> dict:
        """Magnum configuration context."""
        username, password = self.charm.domain_admin_credentials
        return {
            "domain_name": self.charm.domain_name,
            "domain_admin_user": username,
            "domain_admin_password": password,
        }


class MagnumConductorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for magnum worker."""

    def __init__(self, *args, **kwargs):
        """Initialize handler."""
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Magnum conductor service layer.

        :returns: pebble layer configuration for worker service
        :rtype: dict
        """
        return {
            "summary": "magnum worker layer",
            "description": "pebble configuration for magnum conductor",
            "services": {
                "magnum-conductor": {
                    "override": "replace",
                    "summary": "magnum conductor",
                    "command": "magnum-conductor",
                    "user": "magnum",
                    "group": "magnum",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/magnum.conf",
                "magnum",
                "magnum",
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/api-paste.ini",
                "magnum",
                "magnum",
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/keystone_auth_default_policy.json",
                "magnum",
                "magnum",
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/policy.json",
                "magnum",
                "magnum",
            ),
        ]

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logging.debug("Service checks enabled for magnum worker")
            return super().service_ready
        else:
            logging.debug("Service checks disabled for magnum worker")
            return self.pebble_ready


class MagnumOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "magnum-api"
    wsgi_admin_script = "/usr/bin/magnum-api-wsgi"
    wsgi_public_script = "/usr/bin/magnum-api-wsgi"
    mandatory_relations = {
        "database",
        "amqp",
        "identity-service",
        "ingress-public",
        "identity-ops",
    }

    db_sync_cmds = [["sudo", "-u", "magnum", "magnum-db-manage", "upgrade"]]

    def __init__(self, *args, **kwargs):
        """Initialize charm."""
        super().__init__(*args, **kwargs)
        self._state.set_default(identity_ops_ready=False)

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/magnum/magnum.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "magnum"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "magnum"

    @property
    def service_endpoints(self):
        """Service endpoints."""
        return [
            {
                "service_name": "magnum",
                "type": "container-infra",
                "description": "OpenStack Magnum API",
                "internal_url": self.internal_url,
                "public_url": self.public_url,
                "admin_url": self.admin_url,
            }
        ]

    @property
    def default_public_ingress_port(self) -> int:
        """Default public ingress port."""
        return 9511

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend([MagnumConfigurationContext(self, "magnum")])
        return _cadapters

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
    ) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = super().get_pebble_handlers()
        pebble_handlers.extend(
            [
                MagnumConductorPebbleHandler(
                    self,
                    MAGNUM_CONDUCTOR_CONTAINER,
                    "magnum-conductor",
                    [],
                    self.template_dir,
                    self.configure_charm,
                ),
            ]
        )
        return pebble_handlers

    @property
    def domain_name(self) -> str:
        """Domain name to create."""
        return "magnum"

    @property
    def domain_admin_user(self) -> str:
        """User to manage users and projects in domain_name."""
        return "magnum_domain_admin"

    @property
    def domain_admin_credentials(self) -> tuple:
        """Credentials for domain admin user."""
        credentials_id = self._get_domain_admin_credentials_secret()
        credentials = self.model.get_secret(id=credentials_id)
        username = credentials.get_content().get("username")
        user_password = credentials.get_content().get("password")
        return (username, user_password)

    def _get_domain_admin_credentials_secret(self) -> str:
        """Get domain admin secret."""
        label = f"{CREDENTIALS_SECRET_PREFIX}{self.domain_admin_user}"
        credentials_id = self.peers.get_app_data(label)

        if not credentials_id:
            credentials_id = self._retrieve_or_set_secret(
                self.domain_admin_user,
            )

        return credentials_id

    def _grant_domain_admin_credentials_secret(
        self,
        relation: Relation,
    ) -> None:
        """Grant secret access to the related units."""
        credentials_id = None
        try:
            credentials_id = self._get_domain_admin_credentials_secret()
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

    def _get_magnum_domain_ops(self) -> list:
        """Generate ops request for domain setup."""
        credentials_id = self._get_domain_admin_credentials_secret()
        ops = [
            # Create domain magnum
            {
                "name": "create_domain",
                "params": {"name": "magnum", "enable": True},
            },
            # Create role magnum_domain_admin
            {"name": "create_role", "params": {"name": "magnum_domain_admin"}},
            # Create user magnum
            {
                "name": "create_user",
                "params": {
                    "name": self.domain_admin_user,
                    "password": credentials_id,
                    "domain": "magnum",
                },
            },
            # Grant role admin to magnum_domain_admin user
            {
                "name": "grant_role",
                "params": {
                    "role": "admin",
                    "domain": "magnum",
                    "user": self.domain_admin_user,
                    "user_domain": "magnum",
                },
            },
        ]
        return ops

    def _handle_initial_magnum_domain_setup_response(
        self,
        event: RelationEvent,
    ) -> None:
        """Handle domain setup response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get(
                "ops",
                [],
            )
        } == {0}:
            logger.debug(
                "Initial magnum domain setup commands completed,"
                " running configure charm"
            )
            self.configure_charm(event)

    def handle_keystone_ops(self, event: RelationEvent) -> None:
        """Event handler for identity ops."""
        if isinstance(event, identity_resource.IdentityOpsProviderReadyEvent):
            self._state.identity_ops_ready = True

            if not self.unit.is_leader():
                return

            # Send op request only by leader unit
            ops = self._get_magnum_domain_ops()
            id_ = self.hash_ops(ops)
            self._grant_domain_admin_credentials_secret(event.relation)
            request = {
                "id": id_,
                "tag": "initial_magnum_domain_setup",
                "ops": ops,
            }
            logger.debug(f"Sending ops request: {request}")
            self.id_ops.interface.request_ops(request)
        elif isinstance(
            event,
            identity_resource.IdentityOpsProviderGoneAwayEvent,
        ):
            self._state.identity_ops_ready = False
        elif isinstance(event, identity_resource.IdentityOpsResponseEvent):
            if not self.unit.is_leader():
                return
            response = self.id_ops.interface.response
            logger.debug(f"Got response from keystone: {response}")
            request_tag = response.get("tag")
            if request_tag == "initial_magnum_domain_setup":
                self._handle_initial_magnum_domain_setup_response(event)


if __name__ == "__main__":
    main(MagnumOperatorCharm)
