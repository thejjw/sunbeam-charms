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

import functools
import logging

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


class SnapInstallationError(Exception):
    """Custom Exception raised when snap installation fails."""

    pass


@sunbeam_tracing.trace_sunbeam_charm(extra_types=(snap.SnapCache, snap.Snap))
class EpaOrchestratorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "epa-orchestrator"

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        framework.observe(self.on.install, self._on_install)

    def _on_install(self, _: ops.InstallEvent):
        """Handle install event."""
        with sunbeam_guard.guard(
            self, "Executing install hook handler", False
        ):
            self.ensure_snap_epa_orchestrator()

    def ensure_snap_epa_orchestrator(self):
        """Install the snap epa-orchestrator."""
        config = self.model.config.get
        try:
            cache = self.get_snap_cache()
            epa_orchestrator = cache["epa-orchestrator"]
            if not epa_orchestrator.present:
                epa_orchestrator.ensure(
                    snap.SnapState.Latest, channel=config("snap-channel")
                )
        except (snap.SnapError, snap.SnapNotFoundError) as e:
            logger.error(
                f"An exception occurred while installing snap epa-orchestrator. Reason: {e.message}"
            )
            raise SnapInstallationError(
                f"Snap epa-orchestrator failed. Reason: {e.message}"
            )

    @functools.cache
    def get_snap_cache(self) -> snap.SnapCache:
        """Returns the snap cache."""
        return snap.SnapCache()

    def configure_unit(self, event: ops.EventBase) -> None:
        """Configure the unit."""
        self.check_leader_ready()
        self.ensure_snap_epa_orchestrator()
        self._state.unit_bootstrapped = True


if __name__ == "__main__":  # pragma: nocover
    ops.main(EpaOrchestratorCharm)
