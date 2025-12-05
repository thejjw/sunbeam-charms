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

"""Octavia Operator Charm.

This charm provide Octavia services as part of an OpenStack deployment
"""

import hashlib
import json
import logging
from typing import (
    List,
)

import charms.keystone_k8s.v0.identity_resource as identity_resource
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops.framework import (
    StoredState,
)

logger = logging.getLogger(__name__)
OCTAVIA_API_CONTAINER = "octavia-api"
OCTAVIA_DRIVER_AGENT_CONTAINER = "octavia-driver-agent"
OCTAVIA_HOUSEKEEPING_CONTAINER = "octavia-housekeeping"
OCTAVIA_AGENT_SOCKET_DIR = "/var/run/octavia"


@sunbeam_tracing.trace_type
class OctaviaDriverAgentPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Octavia Driver Agent."""

    def get_layer(self) -> dict:
        """Octavia Driver Agent service layer.

        :returns: pebble layer configuration for driver agent service
        :rtype: dict
        """
        return {
            "summary": "octavia driver agent layer",
            "description": "pebble configuration for octavia-driver-agent service",
            "services": {
                "octavia-driver-agent": {
                    "override": "replace",
                    "summary": "Octavia Driver Agent",
                    "command": "octavia-driver-agent",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_type
class OctaviaHousekeepingPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Octavia Housekeeping."""

    def get_layer(self) -> dict:
        """Octavia Housekeeping service layer.

        :returns: pebble layer configuration for housekeeping service
        :rtype: dict
        """
        return {
            "summary": "octavia housekeeping layer",
            "description": "pebble configuration for octavia-housekeeping service",
            "services": {
                "octavia-housekeeping": {
                    "override": "replace",
                    "summary": "Octavia Housekeeping",
                    "command": "octavia-housekeeping",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_type
class OVNContext(sunbeam_config_contexts.ConfigContext):
    """OVN configuration."""

    def context(self) -> dict:
        """Configuration context."""
        return {
            "ovn_key": "/etc/octavia/ovn_private_key.pem",
            "ovn_cert": "/etc/octavia/ovn_certificate.pem",
            "ovn_ca_cert": "/etc/octavia/ovn_ca_cert.pem",
        }


class OctaviaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "octavia-api"
    wsgi_admin_script = "/usr/bin/octavia-wsgi"
    wsgi_public_script = "/usr/bin/octavia-wsgi"

    db_sync_cmds = [
        [
            "octavia-db-manage",
            "--config-file",
            "/etc/octavia/octavia.conf",
            "upgrade",
            "head",
        ]
    ]

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/octavia/octavia.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "octavia"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "octavia"

    @property
    def service_endpoints(self):
        """Return service endpoints for the service."""
        return [
            {
                "service_name": "octavia",
                "type": "load-balancer",
                "description": "OpenStack Octavia API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 9876

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                OCTAVIA_API_CONTAINER,
                self.service_name,
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            OctaviaHousekeepingPebbleHandler(
                self,
                OCTAVIA_HOUSEKEEPING_CONTAINER,
                "octavia-housekeeping",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("identity-ops", handlers):
            self.id_ops = sunbeam_rhandlers.IdentityResourceRequiresHandler(
                self,
                "identity-ops",
                self.handle_keystone_ops,
                mandatory="identity-ops" in self.mandatory_relations,
            )
            handlers.append(self.id_ops)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        # Update with configs that are common for all containers
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/octavia.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/api_audit_map.conf",
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

    def handle_keystone_ops(self, event: ops.EventBase) -> None:
        """Event handler for identity ops."""
        if isinstance(event, identity_resource.IdentityOpsProviderReadyEvent):
            self._state.identity_ops_ready = True

            if not self.unit.is_leader():
                return

            # Send op request only by leader unit
            ops = self._get_octavia_role_ops()
            id_ = self.hash_ops(ops)
            request = {
                "id": id_,
                "tag": "octavia_roles_setup",
                "ops": ops,
            }
            logger.debug("Sending ops request: %r", request)
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
            logger.debug("Got response from keystone: %r", response)
            request_tag = response.get("tag")
            if request_tag == "octavia_roles_setup":
                self._handle_octavia_roles_setup(event)

    def _handle_octavia_roles_setup(
        self,
        event: ops.EventBase,
    ) -> None:
        """Handle roles setup response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get(
                "ops",
                [],
            )
        } != {0}:
            logger.error("Failed to setup octavia roles")

    def hash_ops(self, ops: list) -> str:
        """Return the sha1 of the requested ops."""
        return hashlib.sha1(json.dumps(ops).encode()).hexdigest()

    def _get_octavia_role_ops(self) -> list:
        """Generate ops request for creation of roles."""
        roles = [
            "load-balancer_observer",
            "load-balancer_global_observer",
            "load-balancer_member",
            "load-balancer_quota_admin",
            "load-balancer_admin",
        ]
        ops = [
            {"name": "create_role", "params": {"name": name}} for name in roles
        ]
        return ops

    def _on_upgrade_charm(self, event: ops.framework.EventBase):
        """Handle the upgrade charm event."""
        logger.info("Handling upgrade-charm event")
        self.certs.validate_and_regenerate_certificates_if_needed(
            self.get_tls_certificate_requests()
        )


@sunbeam_tracing.trace_sunbeam_charm
class OctaviaOVNOperatorCharm(OctaviaOperatorCharm):
    """Charm the Octavia service with OVN provider."""

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(OVNContext(self, "ovn"))
        return contexts

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = super().get_pebble_handlers()
        pebble_handlers.append(
            OctaviaDriverAgentPebbleHandler(
                self,
                OCTAVIA_DRIVER_AGENT_CONTAINER,
                "octavia-driver-agent",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
        )
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_rhandlers.OVSDBCMSRequiresHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                external_connectivity=self.remote_external_access,
                mandatory="ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        cc_configs = super().default_container_configs()
        cc_configs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/octavia/ovn_private_key.pem",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/octavia/ovn_certificate.pem",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/octavia/ovn_ca_cert.pem",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
            ]
        )
        return cc_configs

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.open_ports()
        self.configure_containers()
        self.run_db_sync()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        for container in [
            OCTAVIA_API_CONTAINER,
            OCTAVIA_DRIVER_AGENT_CONTAINER,
        ]:
            ph = self.get_named_pebble_handler(container)
            ph.execute(
                [
                    "chown",
                    f"{self.service_user}:{self.service_group}",
                    OCTAVIA_AGENT_SOCKET_DIR,
                ]
            )
        self._state.unit_bootstrapped = True


if __name__ == "__main__":  # pragma: nocover
    ops.main(OctaviaOVNOperatorCharm)
