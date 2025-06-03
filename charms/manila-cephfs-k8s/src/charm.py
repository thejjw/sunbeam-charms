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

"""Manila Share (Cephfs) Operator Charm.

This charm provides Cephfs-based Manila Share capabilities for OpenStack.
"""

import logging
from typing import (
    List,
    Mapping,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

MANILA_SHARE_CONTAINER = "manila-share"


@sunbeam_tracing.trace_type
class ManilaSharePebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Manila Share."""

    def get_layer(self) -> dict:
        """Manila Share service layer.

        :returns: pebble layer configuration for manila-share service
        :rtype: dict
        """
        return {
            "summary": "manila share layer",
            "description": "pebble configuration for manila-share service",
            "services": {
                "manila-share": {
                    "override": "replace",
                    "summary": "Manila Share",
                    "command": "manila-share",
                    "user": "manila",
                    "group": "manila",
                },
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class ManilaShareCephfsCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "manila-share"

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers(handlers or [])
        return handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        return contexts

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/manila.conf",
                "root",
                "manila",
                0o640,
            ),
        ]
        return _cconfigs

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for manila services."""
        return {"database": "manila"}

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            ManilaSharePebbleHandler(
                self,
                MANILA_SHARE_CONTAINER,
                MANILA_SHARE_CONTAINER,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "manila"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "manila"


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaShareCephfsCharm)
