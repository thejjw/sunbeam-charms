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
    Dict,
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
import charms.manila_k8s.v0.manila as manila_k8s

logger = logging.getLogger(__name__)

MANILA_SHARE_CONTAINER = "manila-share"
MANILA_RELATION_NAME = "manila"
SHARE_PROTOCOL_CEPHFS = "CEPHFS"


@sunbeam_tracing.trace_type
class CephfsConfigurationContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set cephfs parameters."""

    def context(self) -> dict:
        """Generate configuration information for cephfs config."""
        ctxt = {
            "driver_handles_share_servers": False,
            "share_backend_name": "CEPHFSNATIVE1",
            "cephfs_auth_id": "manila",
            "cephfs_cluster_name": "ceph",
            "cephfs_filesystem_name": "cephfs",
        }

        return ctxt


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

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "manila",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
class ManilaProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for manila relation."""

    def setup_event_handler(self):
        """Configure event handlers for manila service relation."""
        logger.debug("Setting up manila event handler")
        handler = sunbeam_tracing.trace_type(manila_k8s.ManilaProvides)(
            self.charm,
            self.relation_name,
            SHARE_PROTOCOL_CEPHFS,
        )

        self.framework.observe(
            handler.on.manila_connected,
            self._on_manila_connected,
        )
        self.framework.observe(
            handler.on.manila_goneaway,
            self._on_manila_goneaway,
        )

        return handler

    def _on_manila_connected(self, event: manila_k8s.ManilaConnectedEvent) -> None:
        """Handle ManilaConnectedEvent event."""
        self.callback_f(event)

    def _on_manila_goneaway(self, event: manila_k8s.ManilaGoneAwayEvent) -> None:
        """Handle ManilaGoneAwayEvent event."""
        pass

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


@sunbeam_tracing.trace_sunbeam_charm
class ManilaShareCephfsCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "manila-share"

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers(handlers or [])

        if self.can_add_handler(MANILA_RELATION_NAME, handlers):
            self.manila_handler = ManilaProvidesHandler(
                self,
                MANILA_RELATION_NAME,
                self.set_config_from_event,
            )
            handlers.append(self.manila_handler)

        return handlers

    def set_config_from_event(self, event: ops.framework.EventBase) -> None:
        pass

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(CephfsConfigurationContext(self, "cephfs_config"))
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
            sunbeam_core.ContainerConfigFile(
                "/etc/ceph/ceph.conf",
                "root",
                "manila",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/ceph/manila.keyring",
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
