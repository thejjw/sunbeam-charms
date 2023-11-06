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
from typing import (
    Callable,
    List,
    Mapping,
    Optional,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.heat_k8s.v0.heat_shared_config import (
    HeatSharedConfigChangedEvent,
    HeatSharedConfigProvides,
    HeatSharedConfigRequestEvent,
    HeatSharedConfigRequires,
)
from ops.charm import (
    CharmBase,
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
)

logger = logging.getLogger(__name__)

CREDENTIALS_SECRET_PREFIX = "credentials_"
HEAT_API_CONTAINER = "heat-api"
HEAT_ENGINE_CONTAINER = "heat-engine"
HEAT_API_SERVICE_KEY = "api-service"
HEAT_API_SERVICE_NAME = "heat-api"
HEAT_CFN_SERVICE_NAME = "heat-api-cfn"


class HeatSharedConfigProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for heat shared config relation on provider side."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        """Create a new heat-shared-config handler.

        Create a new HeatSharedConfigProvidesHandler that updates heat config
        auth-encryption-key on the related units.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        """
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for Heat shared config relation."""
        logger.debug("Setting up Heat shared config event handler")
        svc = HeatSharedConfigProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_request,
            self._on_config_request,
        )
        return svc

    def _on_config_request(self, event: HeatSharedConfigRequestEvent) -> None:
        """Handle Config request event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


class HeatSharedConfigRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handle heat shared config relation on the requires side."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = False,
    ):
        """Create a new heat-shared-config handler.

        Create a new HeatSharedConfigRequiresHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        :param mandatory: If the relation is mandatory to proceed with
                          configuring charm
        :type mandatory: bool
        """
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> None:
        """Configure event handlers for Heat shared config relation."""
        logger.debug("Setting up Heat shared config event handler")
        svc = HeatSharedConfigRequires(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_changed,
            self._on_config_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_goneaway,
        )
        return svc

    def _on_config_changed(self, event: RelationEvent) -> None:
        """Handle config_changed  event."""
        logger.debug(
            "Heat shared config provider config changed event received"
        )
        self.callback_f(event)

    def _on_goneaway(self, event: RelationEvent) -> None:
        """Handle gone_away  event."""
        logger.debug("Heat shared config relation is departed/broken")
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.auth_encryption_key)
        except (AttributeError, KeyError):
            return False


class HeatAPIPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Heat API container."""

    def get_layer(self):
        """Heat API service.

        :returns: pebble service layer configuration for heat api service
        :rtype: dict
        """
        if self.charm.service_name == HEAT_CFN_SERVICE_NAME:
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

    @property
    def ready(self) -> bool:
        """Whether the context has all the data is needs."""
        return (
            self.charm.user_id_ops.ready
            and self.charm.get_heat_auth_encryption_key() is not None
        )

    def context(self) -> dict:
        """Heat configuration context."""
        credentials = self.charm.user_id_ops.get_config_credentials()
        heat_auth_encryption_key = self.charm.get_heat_auth_encryption_key()
        if credentials is None or heat_auth_encryption_key is None:
            return {}
        username, password = credentials
        return {
            "stack_domain_name": self.charm.stack_domain_name,
            "stack_domain_admin_user": username,
            "stack_domain_admin_password": password,
            "auth_encryption_key": self.charm.get_heat_auth_encryption_key(),
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
        self.user_id_ops = (
            sunbeam_rhandlers.UserIdentityResourceRequiresHandler(
                self,
                "identity-ops",
                self.configure_charm,
                mandatory="identity-ops" in self.mandatory_relations,
                name=self.stack_domain_admin_user,
                domain=self.stack_domain_name,
                role="admin",
                add_suffix=True,
                extra_ops=self._get_create_role_ops(),
                extra_ops_process=self._handle_create_role_response,
            )
        )
        handlers.append(self.user_id_ops)

        if self.service_name == HEAT_CFN_SERVICE_NAME:
            # heat-config is not a mandatory relation.
            # If instance of heat-k8s deployed with service heat-api-cfn and
            # without any heat-api, heat-api-cfn workload should still come
            # to active using internally generated auth-encryption-key
            self.heat_config_receiver = HeatSharedConfigRequiresHandler(
                self,
                "heat-config",
                self.handle_heat_config_events,
                "heat-config" in self.mandatory_relations,
            )
            handlers.append(self.heat_config_receiver)
        else:
            self.config_svc = HeatSharedConfigProvidesHandler(
                self,
                "heat-service",
                self.set_config_from_event,
            )
            handlers.append(self.config_svc)

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

    def get_heat_auth_encryption_key_secret(self) -> Optional[str]:
        """Return the auth encryption key secret id."""
        return self.leader_get(self.heat_auth_encryption_key)

    def get_heat_auth_encryption_key(self) -> Optional[str]:
        """Return the auth encryption key."""
        secret_id = self.leader_get(self.heat_auth_encryption_key)
        if secret_id:
            key = self.model.get_secret(id=secret_id)
            return key.get_content().get(self.heat_auth_encryption_key)

        return None

    def set_heat_auth_encryption_key(self):
        """Generate and Store the auth encryption key in app data."""
        try:
            label = self.heat_auth_encryption_key
            credentials_id = self.leader_get(label)
            # Auth encryption key already generated, nothing to do
            if credentials_id:
                return

            key = secrets.token_hex(16)
            credentials_secret = self.model.app.add_secret(
                {label: key},
                label=label,
            )
            self.leader_set({label: credentials_secret.id})
        except ModelError as e:
            logger.debug(str(e))

    def configure_charm(self, event):
        """Configure charm.

        Ensure setting the auth key is first as services in container need it
        to start.
        """
        if self.unit.is_leader():
            auth_key = self.get_heat_auth_encryption_key_secret()
            if auth_key:
                logger.debug("Found auth key in leader DB")
            else:
                logger.debug("Creating auth key")
                self.set_heat_auth_encryption_key()
                if self.service_name == HEAT_API_SERVICE_NAME:
                    # Send Auth encryption key over heat-service relation
                    self.set_config_on_update()

        super().configure_charm(event)

    def configure_app_leader(self, event):
        """Configure app leader.

        Ensure setting service_name in peer relation application data if it
        does not exist.
        """
        super().configure_app_leader(event)

        # Update service name in application data
        if not self.leader_get(HEAT_API_SERVICE_KEY):
            self.leader_set({HEAT_API_SERVICE_KEY: self.service_name})

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
            service_name = self.leader_get(HEAT_API_SERVICE_KEY)

        if not service_name:
            service_name = self.config.get("api_service")
            if service_name not in [
                HEAT_API_SERVICE_NAME,
                HEAT_CFN_SERVICE_NAME,
            ]:
                logger.warning(
                    "Config parameter api_service should be one of heat-api, heat-api-cfn, defaulting to heat-api."
                )
                service_name = HEAT_API_SERVICE_NAME

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
        if self.service_name == HEAT_CFN_SERVICE_NAME:
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
        if self.service_name == HEAT_CFN_SERVICE_NAME:
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
        if self.service_name == HEAT_CFN_SERVICE_NAME:
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

    def _get_create_role_ops(self) -> list:
        """Generate ops request for create role."""
        return [
            {
                "name": "create_role",
                "params": {"name": "heat_stack_user"},
            }
        ]

    def _handle_create_role_response(
        self, event: ops.EventBase, response: dict
    ) -> None:
        """Handle response from identity-ops."""
        logger.info("%r", response)
        if {
            op.get("return-code")
            for op in response.get("ops", [])
            if op.get("name") == "create_role"
        } == {0}:
            logger.debug("Heat stack user role has been created.")
        else:
            logger.warning("Heat stack user role creation failed.")

    def _set_config(self, key: str, relation: Relation) -> None:
        """Set config key over the relation."""
        logger.debug(
            f"Setting config on relation {relation.app.name} {relation.name}/{relation.id}"
        )
        try:
            secret = self.model.get_secret(id=key)
            logger.debug(
                f"Granting access to secret {key} for relation "
                f"{relation.app.name} {relation.name}/{relation.id}"
            )
            secret.grant(relation)
            self.config_svc.interface.set_config(
                relation=relation,
                auth_encryption_key=key,
            )
        except (ModelError, SecretNotFoundError) as e:
            logger.debug(
                f"Error during granting access to secret {key} for "
                f"relation {relation.app.name} {relation.name}/{relation.id}: "
                f"{str(e)}"
            )

    def set_config_from_event(self, event: RelationEvent) -> None:
        """Set config in relation data."""
        if not self.unit.is_leader():
            logger.debug("Not a leader unit, skipping set config")
            return

        key = self.get_heat_auth_encryption_key_secret()
        if not key:
            logger.debug("Auth encryption key not yet set, not sending config")
            return

        self._set_config(key, event.relation)

    def set_config_on_update(self) -> None:
        """Set config on relation on update of local data."""
        logger.debug(
            "Update config on all connected heat-shared-config relations"
        )
        key = self.get_heat_auth_encryption_key_secret()
        if not key:
            logger.info("Auth encryption key not yet set, not sending config")
            return

        # Send config on all joined heat-service relations
        for relation in self.framework.model.relations["heat-service"]:
            self._set_config(key, relation)

    def handle_heat_config_events(self, event: RelationEvent) -> None:
        """Handle heat config events.

        This function is called only for heat-k8s instances with api_service
        heat-api-cfn. Receives auth_encryption_key update from heat-api
        service via interface heat-service.
        Update the peer appdata and configure charm for the leader unit.
        For non-leader units, peer changed event should get triggered which
        calls configure_charm.
        """
        logger.debug(f"Received event {event}")
        if isinstance(event, HeatSharedConfigChangedEvent):
            key = self.heat_config_receiver.interface.auth_encryption_key
            # Update appdata with auth-encryption-key from heat-api
            if self.unit.is_leader():
                logger.debug(
                    "Update Auth encryption key in appdata received from "
                    "heat-service relation event"
                )
                self.leader_set({self.heat_auth_encryption_key: key})
                self.configure_charm(event)
            else:
                logger.debug("Not a leader unit, nothing to do")


if __name__ == "__main__":
    main(HeatOperatorCharm)
