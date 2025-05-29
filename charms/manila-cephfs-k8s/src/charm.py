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
    Callable,
    List,
    Mapping,
)

import charms.ceph_nfs_client.v0.ceph_nfs_client as ceph_nfs_client
import charms.manila_k8s.v0.manila as manila_k8s
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

MANILA_SHARE_CONTAINER = "manila-share"
CEPH_NFS_RELATION_NAME = "ceph-nfs"
MANILA_RELATION_NAME = "manila"
SHARE_PROTOCOL_NFS = "NFS"


@sunbeam_tracing.trace_type
class CephfsConfigurationContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set cephfs parameters."""

    def context(self) -> dict:
        """Generate configuration information for cephfs config."""
        ctxt = self.charm.get_cephfs_config()
        return ctxt


@sunbeam_tracing.trace_type
class CephNfsRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handles the ceph-nfs relation on the requires side."""

    interface: "ceph_nfs_client.CephNfsRequires"

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        """Constructor for CephNfsRequiresHandler.

        Creates a new CephNfsRequiresHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        """
        super().__init__(charm, relation_name, callback_f, mandatory=True)

    def setup_event_handler(self):
        """Configure event handlers for the cephfs relation."""
        logger.debug("Setting up ceph-nfs event handler")
        ceph_nfs_handler = sunbeam_tracing.trace_type(
            ceph_nfs_client.CephNfsRequires,
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ceph_nfs_handler.on.ceph_nfs_connected,
            self._ceph_nfs_connected,
        )
        self.framework.observe(
            ceph_nfs_handler.on.ceph_nfs_departed,
            self._ceph_nfs_departed,
        )
        return ceph_nfs_handler

    def _ceph_nfs_connected(self, event) -> None:
        """Handles ceph-nfs connected events."""
        self.callback_f(event)

    def _ceph_nfs_departed(self, event) -> None:
        """Handles ceph-nfs departed events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Interface ready for use."""
        relation = self.model.get_relation(self.relation_name)
        if not relation or not relation.data[relation.app].get("client"):
            return False

        return True


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


@sunbeam_tracing.trace_type
class ManilaProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for manila relation."""

    interface: "manila_k8s.ManilaProvides"

    def setup_event_handler(self):
        """Configure event handlers for manila service relation."""
        logger.debug("Setting up manila event handler")
        handler = sunbeam_tracing.trace_type(manila_k8s.ManilaProvides)(
            self.charm,
            self.relation_name,
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

    def _on_manila_connected(
        self, event: manila_k8s.ManilaConnectedEvent
    ) -> None:
        """Handle ManilaConnectedEvent event."""
        self.callback_f(event)

    def _on_manila_goneaway(
        self, event: manila_k8s.ManilaGoneAwayEvent
    ) -> None:
        """Handle ManilaGoneAwayEvent event."""
        pass

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        # This relation is not ready if there is no ceph-nfs relation.
        relation = self.model.get_relation(CEPH_NFS_RELATION_NAME)
        if not relation or not relation.data[relation.app].get("client"):
            return False

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

        if self.can_add_handler(CEPH_NFS_RELATION_NAME, handlers):
            self.ceph_nfs = CephNfsRequiresHandler(
                self,
                CEPH_NFS_RELATION_NAME,
                self.handle_ceph_nfs,
            )
            handlers.append(self.ceph_nfs)

        if self.can_add_handler(MANILA_RELATION_NAME, handlers):
            self.manila_handler = ManilaProvidesHandler(
                self,
                MANILA_RELATION_NAME,
                self.handle_manila,
            )
            handlers.append(self.manila_handler)

        return handlers

    def handle_ceph_nfs(self, event: ops.framework.EventBase) -> None:
        """Handle the ceph-nfs relation changes."""
        self.configure_charm(event)
        self.handle_manila(event)

    def handle_manila(self, event: ops.framework.EventBase) -> None:
        """Handle the manila relation data."""
        if self.ceph_nfs.ready:
            self.manila_handler.interface.update_share_protocol(
                SHARE_PROTOCOL_NFS
            )
        else:
            # ceph-nfs relation not ready, remove relation data, if set.
            self.manila_handler.interface.update_share_protocol(None)

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

    def get_cephfs_config(self) -> dict:
        """Get the cephfs-related config from the the relation data."""
        data = self.ceph_nfs.interface.get_relation_data()
        if not data:
            return {}

        return {
            "share_backend_name": "CEPHNFS",
            "cephfs_auth_id": data["client"].lstrip("client."),
            "client_key": data["keyring"],
            "cephfs_cluster_name": data["cluster-id"],
            "cephfs_filesystem_name": data["volume"],
            "mon_hosts": ",".join(data["mon_hosts"]),
        }


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaShareCephfsCharm)
