#!/usr/bin/env python3
#
# Copyright 2025 Canonical Ltd.
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

"""nova-compute for Ironic Operator Charm.

This charm provides nova-compute service for Ironic as part of an OpenStack
deployment.
"""

import logging
from typing import (
    List,
    Mapping,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

NOVA_IRONIC_CONTAINER = "nova-ironic"


@sunbeam_tracing.trace_type
class NovaIronicPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for nova-ironic."""

    def get_layer(self) -> dict:
        """nova-ironic service layer.

        :returns: pebble layer configuration for the nova-ironic service
        :rtype: dict
        """
        return {
            "summary": "nova-ironic layer",
            "description": "pebble configuration for nova-ironic service",
            "services": {
                "nova-ironic": {
                    "override": "replace",
                    "summary": "Nova Compute for Ironic",
                    "command": "nova-compute --config-file /etc/nova/nova.conf",
                    "user": "nova",
                    "group": "nova",
                }
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class NovaIronicOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "nova-ironic"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "nova"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "nova"

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for nova-ironic services."""
        return {"database": "nova"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            NovaIronicPebbleHandler(
                self,
                NOVA_IRONIC_CONTAINER,
                NOVA_IRONIC_CONTAINER,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for operator."""
        handlers = super().get_relation_handlers(handlers or [])

        self.ironic_svc = sunbeam_rhandlers.ServiceReadinessRequiresHandler(
            self,
            "ironic-api",
            self.configure_charm,
            "ironic-api" in self.mandatory_relations,
        )
        handlers.append(self.ironic_svc)

        return handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/rootwrap.conf",
                "root",
                "nova",
                0o640,
            ),
        ]
        return _cconfigs


if __name__ == "__main__":  # pragma: nocover
    ops.main(NovaIronicOperatorCharm)
