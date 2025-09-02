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

"""Ironic Conductor Operator Charm.

This charm provides Ironic Conductor service as part of an OpenStack
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
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

IRONIC_CONDUCTOR_CONTAINER = "ironic-conductor"


@sunbeam_tracing.trace_type
class IronicConductorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Ironic Conductor."""

    def get_layer(self) -> dict:
        """Ironic Conductor service layer.

        :returns: pebble layer configuration for the ironic-conductor service
        :rtype: dict
        """
        return {
            "summary": "ironic conductor layer",
            "description": "pebble configuration for ironic-conductor service",
            "services": {
                "ironic-conductor": {
                    "override": "replace",
                    "summary": "Ironic Conductor",
                    "command": "ironic-conductor",
                    "user": "ironic",
                    "group": "ironic",
                }
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class IronicConductorOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "ironic-conductor"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "ironic"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "ironic"

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for ironic services."""
        return {"database": "ironic"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            IronicConductorPebbleHandler(
                self,
                IRONIC_CONDUCTOR_CONTAINER,
                IRONIC_CONDUCTOR_CONTAINER,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/ironic.conf",
                "root",
                "ironic",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/rootwrap.conf",
                "root",
                "ironic",
                0o640,
            ),
        ]
        return _cconfigs


if __name__ == "__main__":  # pragma: nocover
    ops.main(IronicConductorOperatorCharm)
