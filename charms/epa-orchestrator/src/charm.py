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

"""EPA Orchestrator Charm operator.

This charm is used to install the epa-orchestrator snap as a part of the openstack deployment.
"""

import logging
from typing import (
    List,
)

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class SunbeamMachineRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for sunbeam-machine relation on the requires side."""

    def setup_event_handler(self):
        """Noop event handler for sunbeam-machine relation."""
        return ops.framework.Object(self.charm, self.relation_name)

    def interface_properties(self) -> dict:
        """Return empty interface properties."""
        return {}

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        relations = self.model.relations.get(self.relation_name, [])
        return len(relations) > 0


@sunbeam_tracing.trace_sunbeam_charm(extra_types=(snap.SnapCache, snap.Snap))
class EpaOrchestratorCharm(sunbeam_charm.OSBaseOperatorCharmSnap):
    """Charm the service."""

    service_name = "epa-orchestrator"

    @property
    def snap_name(self) -> str:
        """Returns the snap name."""
        return str(self.model.config["snap-name"])

    @property
    def snap_channel(self) -> str:
        """Returns the snap channel."""
        return str(self.model.config["snap-channel"])

    def ensure_services_running(self, enable: bool = True) -> None:
        """Override to prevent service start - this snap has no services."""
        logger.debug(
            "Skipping service start - %s snap has no services", self.snap_name
        )
        pass

    def stop_services(self, relation: set[str] | None = None) -> None:
        """Override to prevent service stop - this snap has no services."""
        logger.debug(
            "Skipping service stop - %s snap has no services", self.snap_name
        )
        pass

    def relation_ready(self, event) -> None:
        """Noop callback for sunbeam-machine relation."""
        pass

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("sunbeam-machine", handlers):
            self.sunbeam_machine = SunbeamMachineRequiresHandler(
                self, "sunbeam-machine", self.relation_ready, mandatory=True
            )
            handlers.append(self.sunbeam_machine)

        return super().get_relation_handlers(handlers)


if __name__ == "__main__":  # pragma: nocover
    ops.main(EpaOrchestratorCharm)
