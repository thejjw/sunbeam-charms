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
import socket
from typing import (
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
import ops_sunbeam.tracing as sunbeam_tracing
from ops.framework import (
    StoredState,
)
from ops.model import (
    ModelError,
)

logger = logging.getLogger(__name__)

HEAT_API_CONTAINER = "heat-api"
HEAT_API_CFN_CONTAINER = "heat-api-cfn"
HEAT_ENGINE_CONTAINER = "heat-engine"
HEAT_API_INGRESS_NAME = "heat"
HEAT_API_CFN_INGRESS_NAME = "heat-cfn"
HEAT_API_PORT = 8004
HEAT_API_CFN_PORT = 8000


@sunbeam_tracing.trace_type
class HeatAPIPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Heat API container."""

    def get_layer(self):
        """Heat API service.

        :returns: pebble service layer configuration for heat api service
        :rtype: dict
        """
        return {
            "summary": "heat api layer",
            "description": "pebble configuration for heat api service",
            "services": {
                "heat-api": {
                    "override": "replace",
                    "summary": "Heat API",
                    "command": "heat-api",
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
                        "url": f"http://localhost:{HEAT_API_PORT}/healthcheck"
                    },
                },
            }
        }


@sunbeam_tracing.trace_type
class HeatCfnAPIPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Heat CFN API container."""

    def get_layer(self):
        """Heat CFN API service.

        :returns: pebble service layer configuration for heat cfn api service
        :rtype: dict
        """
        return {
            "summary": "heat api cfn layer",
            "description": "pebble configuration for heat api cfn service",
            "services": {
                "heat-api-cfn": {
                    "override": "replace",
                    "summary": "Heat API CFN",
                    "command": "heat-api-cfn --config-file /etc/heat/heat-api-cfn.conf",
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
                        "url": f"http://localhost:{HEAT_API_CFN_PORT}/healthcheck"
                    },
                },
            }
        }


@sunbeam_tracing.trace_type
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
                    "user": "heat",
                    "group": "heat",
                }
            },
        }


@sunbeam_tracing.trace_type
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
            "ingress_path": f"/{self.charm.model.name}-{HEAT_API_INGRESS_NAME}",
            "cfn_ingress_path": f"/{self.charm.model.name}-{HEAT_API_CFN_INGRESS_NAME}",
        }


@sunbeam_tracing.trace_sunbeam_charm
class HeatOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    wsgi_admin_script = "/usr/bin/heat-wsgi-api"
    wsgi_public_script = "/usr/bin/heat-wsgi-api"
    heat_auth_encryption_key = "auth-encryption-key"

    db_sync_cmds = [["heat-manage", "db_sync"]]

    def __init__(self, framework):
        super().__init__(framework)
        self.traefik_route_public = None
        self.traefik_route_internal = None
        self._state.set_default(identity_ops_ready=False)
        self.framework.observe(
            self.on.peers_relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.on["peers"].relation_departed, self._on_peer_relation_departed
        )

    def _on_peer_relation_created(self, event: ops.EventBase) -> None:
        logger.info("Setting peer unit data")
        self.peers.set_unit_data({"host": socket.getfqdn()})

    def _on_peer_relation_departed(self, event: ops.EventBase) -> None:
        self.handle_traefik_ready(event)

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

        self.traefik_route_public = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-public",
            self.handle_traefik_ready,
            "traefik-route-public" in self.mandatory_relations,
        )
        handlers.append(self.traefik_route_public)
        self.traefik_route_internal = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-internal",
            # NOTE: self.configure_charm will call self.handle_traefik_ready.
            self.configure_charm,
            "traefik-route-internal" in self.mandatory_relations,
        )
        handlers.append(self.traefik_route_internal)
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
                self.heat_api_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            HeatCfnAPIPebbleHandler(
                self,
                HEAT_API_CFN_CONTAINER,
                "heat-api-cfn",
                self.heat_api_cfn_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            HeatEnginePebbleHandler(
                self,
                HEAT_ENGINE_CONTAINER,
                "heat-engine",
                self.heat_api_container_configs(),
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
            return key.get_content(refresh=True).get(
                self.heat_auth_encryption_key
            )

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

            self.handle_traefik_ready(event)

        super().configure_charm(event)

    @property
    def traefik_config(self) -> dict:
        """Config to publish to traefik."""
        model = self.model.name
        router_cfg = {}
        # Add routers for both heat-api and heat-api-cfn
        for app in HEAT_API_INGRESS_NAME, HEAT_API_CFN_INGRESS_NAME:
            router_cfg.update(
                {
                    f"juju-{model}-{app}-router": {
                        "rule": f"PathPrefix(`/{model}-{app}`)",
                        "service": f"juju-{model}-{app}-service",
                        "entryPoints": ["web"],
                    },
                    f"juju-{model}-{app}-router-tls": {
                        "rule": f"PathPrefix(`/{model}-{app}`)",
                        "service": f"juju-{model}-{app}-service",
                        "entryPoints": ["websecure"],
                        "tls": {},
                    },
                }
            )

        # Get host key value from all units
        hosts = self.peers.get_all_unit_values(
            key="host", include_local_unit=True
        )
        api_lb_servers = [
            {"url": f"http://{host}:{HEAT_API_PORT}"} for host in hosts
        ]
        cfn_lb_servers = [
            {"url": f"http://{host}:{HEAT_API_CFN_PORT}"} for host in hosts
        ]
        # Add services for heat-api and heat-api-cfn
        service_cfg = {
            f"juju-{model}-{HEAT_API_INGRESS_NAME}-service": {
                "loadBalancer": {"servers": api_lb_servers},
            },
            f"juju-{model}-{HEAT_API_CFN_INGRESS_NAME}-service": {
                "loadBalancer": {"servers": cfn_lb_servers},
            },
        }

        config = {
            "http": {
                "routers": router_cfg,
                "services": service_cfg,
            },
        }
        return config

    def _update_service_endpoints(self):
        try:
            if self.id_svc.update_service_endpoints:
                logger.info(
                    "Updating service endpoints after ingress relation changed"
                )
                self.id_svc.update_service_endpoints(self.service_endpoints)
        except (AttributeError, KeyError):
            pass

    def handle_traefik_ready(self, event: ops.EventBase):
        """Handle Traefik route ready callback."""
        if not self.unit.is_leader():
            logger.debug(
                "Not a leader unit, not updating traefik route config"
            )
            return

        if (
            self.traefik_route_public
            and self.traefik_route_public.interface.is_ready()
        ):
            logger.debug("Sending traefik config for public interface")
            self.traefik_route_public.interface.submit_to_traefik(
                config=self.traefik_config
            )

            if self.traefik_route_public.ready:
                self._update_service_endpoints()

        if (
            self.traefik_route_internal
            and self.traefik_route_internal.interface.is_ready()
        ):
            logger.debug("Sending traefik config for internal interface")
            self.traefik_route_internal.interface.submit_to_traefik(
                config=self.traefik_config
            )

            if self.traefik_route_internal.ready:
                self._update_service_endpoints()

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
        """Service name."""
        return "heat"

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
        return [
            {
                "service_name": HEAT_API_CFN_INGRESS_NAME,
                "type": "cloudformation",
                "description": "OpenStack Heat CloudFormation API",
                "internal_url": f"{self.heat_cfn_internal_url}/v1/$(tenant_id)s",
                "public_url": f"{self.heat_cfn_public_url}/v1/$(tenant_id)s",
                "admin_url": f"{self.heat_cfn_admin_url}/v1/$(tenant_id)s",
            },
            {
                "service_name": HEAT_API_INGRESS_NAME,
                "type": "orchestration",
                "description": "OpenStack Heat API",
                "internal_url": f"{self.heat_internal_url}/v1/$(tenant_id)s",
                "public_url": f"{self.heat_public_url}/v1/$(tenant_id)s",
                "admin_url": f"{self.heat_admin_url}/v1/$(tenant_id)s",
            },
        ]

    @property
    def heat_public_url(self) -> str:
        """Url for accessing the public endpoint for heat service."""
        if self.traefik_route_public and self.traefik_route_public.ready:
            scheme = self.traefik_route_public.interface.scheme
            external_host = self.traefik_route_public.interface.external_host
            public_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{HEAT_API_INGRESS_NAME}"
            )
            return self.add_explicit_port(public_url)
        else:
            return self.heat_internal_url

    @property
    def heat_cfn_public_url(self) -> str:
        """Url for accessing the public endpoint for heat cfn service."""
        if (
            self.traefik_route_public
            and self.traefik_route_public.interface.is_ready()
        ):
            scheme = self.traefik_route_public.interface.scheme
            external_host = self.traefik_route_public.interface.external_host
            public_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{HEAT_API_CFN_INGRESS_NAME}"
            )
            return self.add_explicit_port(public_url)
        else:
            return self.heat_cfn_internal_url

    @property
    def heat_internal_url(self) -> str:
        """Url for accessing the internal endpoint for heat service."""
        if self.traefik_route_internal and self.traefik_route_internal.ready:
            scheme = self.traefik_route_internal.interface.scheme
            external_host = self.traefik_route_internal.interface.external_host
            internal_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{HEAT_API_INGRESS_NAME}"
            )
            return self.add_explicit_port(internal_url)
        else:
            return self.heat_admin_url

    @property
    def heat_cfn_internal_url(self) -> str:
        """Url for accessing the internal endpoint for heat cfn service."""
        if (
            self.traefik_route_internal
            and self.traefik_route_internal.interface.is_ready()
        ):
            scheme = self.traefik_route_internal.interface.scheme
            external_host = self.traefik_route_internal.interface.external_host
            internal_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{HEAT_API_CFN_INGRESS_NAME}"
            )
            return self.add_explicit_port(internal_url)
        else:
            return self.heat_cfn_admin_url

    @property
    def heat_admin_url(self) -> str:
        """Url for accessing the admin endpoint for heat service."""
        hostname = self.model.get_binding(
            "identity-service"
        ).network.ingress_address
        url = f"http://{hostname}:{HEAT_API_PORT}"
        return self.add_explicit_port(url)

    @property
    def heat_cfn_admin_url(self) -> str:
        """Url for accessing the admin endpoint for heat service."""
        hostname = self.model.get_binding(
            "identity-service"
        ).network.ingress_address
        url = f"http://{hostname}:{HEAT_API_CFN_PORT}"
        return self.add_explicit_port(url)

    @property
    def default_public_ingress_port(self):
        """Port for Heat API service."""
        return HEAT_API_PORT

    @property
    def wsgi_container_name(self) -> str:
        """Name of the WSGI application container."""
        return HEAT_API_CONTAINER

    @property
    def stack_domain_name(self) -> str:
        """Domain name for heat template-defined users."""
        return "heat"

    @property
    def stack_domain_admin_user(self) -> str:
        """User to manage users and projects in stack_domain_name."""
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

    def heat_api_container_configs(self):
        """Return base container configs."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/heat.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/api-paste.ini",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/api_audit_map.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                self.service_group,
                0o640,
            ),
        ]

    def heat_api_cfn_container_configs(self):
        """Return base container configs."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/heat-api-cfn.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/api-paste-cfn.ini",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/heat/api_audit_map_cfn.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                self.service_group,
                0o640,
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


if __name__ == "__main__":  # pragma: nocover
    ops.main(HeatOperatorCharm)
