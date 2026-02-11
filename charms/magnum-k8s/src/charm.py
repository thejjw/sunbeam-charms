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

import logging
from functools import (
    cached_property,
)
from typing import (
    TYPE_CHECKING,
    List,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import yaml
from ops.framework import (
    StoredState,
)
from ops.model import (
    ModelError,
    SecretNotFoundError,
)

logger = logging.getLogger(__name__)

CREDENTIALS_SECRET_PREFIX = "credentials_"
MAGNUM_API_CONTAINER = "magnum-api"
MAGNUM_CONDUCTOR_CONTAINER = "magnum-conductor"


@sunbeam_tracing.trace_type
class MagnumConfigurationContext(sunbeam_config_contexts.ConfigContext):
    """Magnum configuration context."""

    if TYPE_CHECKING:
        charm: "MagnumOperatorCharm"

    @property
    def ready(self) -> bool:
        """Whether the context has all the data is needs."""
        return self.charm.user_id_ops.ready and bool(self.charm.kubeconfig)

    def context(self) -> dict:
        """Magnum configuration context."""
        credentials = self.charm.user_id_ops.get_config_credentials()
        if credentials is None:
            return {}
        username, password = credentials
        return {
            "domain_name": self.charm.domain_name,
            "domain_admin_user": username,
            "domain_admin_password": password,
            "kubeconfig": self.charm.kubeconfig or "",
        }


@sunbeam_tracing.trace_type
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
                "/etc/magnum/keystone_auth_default_policy.json",
                "magnum",
                "magnum",
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/policy.json",
                "magnum",
                "magnum",
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "magnum",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/magnum/kubeconfig",
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


@sunbeam_tracing.trace_sunbeam_charm
class MagnumOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "magnum-api"
    wsgi_admin_script = "/usr/bin/magnum-api-wsgi"
    wsgi_public_script = "/usr/bin/magnum-api-wsgi"

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
                "internal_url": self.internal_url + "/v1",
                "public_url": self.public_url + "/v1",
                "admin_url": self.admin_url + "/v1",
            }
        ]

    @property
    def default_public_ingress_port(self) -> int:
        """Default public ingress port."""
        return 9511

    @property
    def ingress_healthcheck_path(self):
        """Healthcheck path for ingress relation."""
        return "/healthcheck"

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend([MagnumConfigurationContext(self, "magnum")])
        return _cadapters

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers(handlers)
        self.user_id_ops = (
            sunbeam_rhandlers.UserIdentityResourceRequiresHandler(
                self,
                "identity-ops",
                self.configure_charm,
                mandatory="identity-ops" in self.mandatory_relations,
                name=self.domain_admin_user,
                domain=self.domain_name,
                role="admin",
                add_suffix=True,
                extra_ops=self._get_create_role_ops(),
                extra_ops_process=self._handle_create_role_response,
            )
        )
        handlers.append(self.user_id_ops)
        return handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/magnum/api-paste.ini",
                    "magnum",
                    "magnum",
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/magnum/api_audit_map.conf",
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
                sunbeam_core.ContainerConfigFile(
                    "/usr/local/share/ca-certificates/ca-bundle.pem",
                    "root",
                    "magnum",
                    0o640,
                ),
            ]
        )
        return _cconfigs

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

    def configure_containers(self) -> None:
        """Configure containers on this unit."""
        if not self.config.get("kubeconfig"):
            raise sunbeam_guard.BlockedExceptionError(
                "Configuration parameter kubeconfig not set"
            )

        if self.kubeconfig is None:
            raise sunbeam_guard.BlockedExceptionError(
                "Error in retrieving kubeconfig"
            )

        super().configure_containers()

    @property
    def domain_name(self) -> str:
        """Domain name to create."""
        return "magnum"

    @property
    def domain_admin_user(self) -> str:
        """User to manage users and projects in domain_name."""
        return "magnum_domain_admin"

    @cached_property
    def kubeconfig(self) -> str | None:
        """Kubeconfig content to connect to k8s management cluster."""
        try:
            kubeconfig_secret = self.model.get_secret(
                id=self.config.get("kubeconfig")
            )
            kubeconfig_secret_content = kubeconfig_secret.get_content()
            kubeconfig_string = kubeconfig_secret_content.get("kubeconfig")
            kubeconfig = yaml.safe_load(kubeconfig_string)
            return yaml.dump(kubeconfig)
        except (SecretNotFoundError, ModelError, yaml.YAMLError) as e:
            logger.info(f"Error in retrieving kubeconfig secret: {e}")
            return None

    def _get_create_role_ops(self) -> list:
        """Generate ops request for create role."""
        return [
            {
                "name": "create_role",
                "params": {"name": "magnum_domain_admin"},
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
            logger.debug("Magnum domain admin role has been created.")
        else:
            logger.warning("Magnum domain admin role creation failed.")


if __name__ == "__main__":  # pragma: nocover
    ops.main(MagnumOperatorCharm)
