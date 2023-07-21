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

import logging
import secrets
import string
from typing import (
    List,
    Mapping,
)

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

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
    }

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


if __name__ == "__main__":
    main(HeatOperatorCharm)
