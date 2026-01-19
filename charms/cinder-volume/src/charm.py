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

"""Cinder Volume Operator Charm.

This charm provide Cinder Volume capabilities for OpenStack.
This charm is responsible for managing the cinder-volume snap, actual
backend configurations are managed by the subordinate charms.
"""

import logging
import typing
from typing import (
    Mapping,
)

import charms.cinder_k8s.v0.storage_backend as sunbeam_storage_backend  # noqa
import charms.cinder_volume.v0.cinder_volume as sunbeam_cinder_volume  # noqa
import charms.operator_libs_linux.v2.snap as snap
import ops
import ops.charm
import ops_sunbeam.charm as charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops_sunbeam import (
    compound_status,
)

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class StorageBackendProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for storage-backend interface type."""

    interface: sunbeam_storage_backend.StorageBackendProvides

    def setup_event_handler(self):
        """Configure event handlers for an storage-backend relation."""
        logger.debug("Setting up Identity Service event handler")
        sb_svc = sunbeam_tracing.trace_type(
            sunbeam_storage_backend.StorageBackendProvides
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(sb_svc.on.api_ready, self._on_ready)
        return sb_svc

    def _on_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check whether storage-backend interface is ready for use."""
        return self.interface.remote_ready()


class CinderVolumeProviderHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for cinder-volume interface type."""

    interface: sunbeam_cinder_volume.CinderVolumeProvides

    def __init__(
        self,
        charm: "CinderVolumeOperatorCharm",
        relation_name: str,
        snap: str,
        callback_f: typing.Callable,
        mandatory: bool = False,
    ) -> None:
        self._snap = snap
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self):
        """Configure event handlers for an cinder-volume relation."""
        logger.debug("Setting up Identity Service event handler")
        cinder_volume = sunbeam_tracing.trace_type(
            sunbeam_cinder_volume.CinderVolumeProvides
        )(
            self.charm,
            self.relation_name,
            self._snap,
        )
        self.framework.observe(cinder_volume.on.driver_ready, self._on_event)
        self.framework.observe(cinder_volume.on.driver_gone, self._on_event)
        return cinder_volume

    def _on_event(self, event: ops.RelationEvent) -> None:
        """Handles cinder-volume change events."""
        self.callback_f(event)

    def update_relation_data(self):
        """Publish snap name to all related cinder-volume interfaces."""
        for relation in self.model.relations[self.relation_name]:
            self.interface.publish_snap(relation)

    @property
    def ready(self) -> bool:
        """Check whether cinder-volume interface is ready for use."""
        relations = self.model.relations[self.relation_name]
        if not relations:
            return False
        for relation in relations:
            if not self.interface.requirer_ready(relation):
                return False
        return True

    def backends(self) -> typing.Sequence[str]:
        """Return a list of backends."""
        backends = []
        for relation in self.model.relations[self.relation_name]:
            if backend := self.interface.requirer_backend(relation):
                backends.append(backend)
        return backends


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeOperatorCharm(charm.OSBaseOperatorCharmSnap):
    """Cinder Volume Operator charm."""

    service_name = "cinder-volume"

    mandatory_relations = {
        "storage-backend",
    }

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(api_ready=False, backends=[])
        self._backend_status = compound_status.Status("backends", priority=10)
        self.status_pool.add(self._backend_status)

    @property
    def snap_name(self) -> str:
        """Return snap name."""
        return str(self.model.config["snap-name"])

    @property
    def snap_channel(self) -> str:
        """Return snap channel."""
        return str(self.model.config["snap-channel"])

    def get_relation_handlers(
        self, handlers: list[relation_handlers.RelationHandler] | None = None
    ) -> list[relation_handlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.sb_svc = StorageBackendProvidesHandler(
            self,
            "storage-backend",
            self.api_ready,
            "storage-backend" in self.mandatory_relations,
        )
        handlers.append(self.sb_svc)
        self.cinder_volume = CinderVolumeProviderHandler(
            self,
            "cinder-volume",
            str(self.model.config["snap-name"]),
            self.backend_changes,
            "cinder-volume" in self.mandatory_relations,
        )
        handlers.append(self.cinder_volume)
        return handlers

    def api_ready(self, event) -> None:
        """Event handler for bootstrap of service when api services are ready."""
        self._state.api_ready = True
        self.configure_charm(event)

    def _find_duplicates(self, backends: typing.Sequence[str]) -> set[str]:
        """Find duplicates in a list of backends."""
        seen = set()
        duplicates = set()
        for backend in backends:
            if backend in seen:
                duplicates.add(backend)
            seen.add(backend)
        return duplicates

    def backend_changes(self, event: ops.RelationEvent) -> None:
        """Event handler for backend changes."""
        relation_backends = self.cinder_volume.backends()

        if duplicates := self._find_duplicates(relation_backends):
            logger.warning(
                "Same instance of `cinder-volume` cannot"
                " serve the same backend multiple times."
            )
            raise sunbeam_guard.BlockedExceptionError(
                f"Duplicate backends: {duplicates}"
            )

        state_backends: set[str] = set(self._state.backends)  # type: ignore

        if leftovers := state_backends.difference(relation_backends):
            logger.debug(
                "Removing backends %s from state",
                leftovers,
            )
            for backend in leftovers:
                self.remove_backend(backend)
                state_backends.remove(backend)
        self._state.backends = sorted(state_backends.union(relation_backends))
        self.configure_charm(event)

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for cinder services."""
        return {"database": "cinder"}

    def configure_snap(self, event) -> None:
        """Run configuration on snap."""
        config = self.model.config.get
        try:
            contexts = self.contexts()
            snap_data = {
                "rabbitmq.url": contexts.amqp.transport_url,
                "database.url": contexts.database.connection,
                "cinder.project-id": contexts.identity_credentials.project_id,
                "cinder.user-id": contexts.identity_credentials.username,
                "cinder.cluster": self.app.name,
                "cinder.image-volume-cache-enabled": config(
                    "image-volume-cache-enabled"
                ),
                "cinder.image-volume-cache-max-size-gb": config(
                    "image-volume-cache-max-size-gb"
                ),
                "cinder.image-volume-cache-max-count": config(
                    "image-volume-cache-max-count"
                ),
                "cinder.default-volume-type": config("default-volume-type"),
                "settings.debug": self.model.config["debug"],
                "settings.enable-telemetry-notifications": self.model.config[
                    "enable-telemetry-notifications"
                ],
            }
        except AttributeError as e:
            raise sunbeam_guard.WaitingExceptionError(
                "Data missing: {}".format(e.name)
            )
        self.set_snap_data(snap_data)
        self.check_serving_backends()

    def check_serving_backends(self):
        """Check if backends are ready to serve."""
        if not self.cinder_volume.backends():
            msg = "Waiting for backends"
            self._backend_status.set(ops.WaitingStatus(msg))
            raise sunbeam_guard.WaitingExceptionError(msg)
        self._backend_status.set(ops.ActiveStatus())

    def remove_backend(self, backend: str):
        """Remove backend from snap."""
        cinder_volume = self.get_snap()
        try:
            cinder_volume.unset(backend)
        except snap.SnapError as e:
            logger.debug(
                "Failed to remove backend %s from snap: %s",
                backend,
                e,
            )


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeOperatorCharm)
