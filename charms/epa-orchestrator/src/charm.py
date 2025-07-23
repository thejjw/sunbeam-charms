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

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


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

    def ensure_services_running(self, *args, **kwargs) -> None:
        """Override to prevent service start - this snap has no services."""
        logger.debug(
            "Skipping service start - %s snap has no services", self.snap_name
        )
        pass

    def stop_services(self, *args, **kwargs) -> None:
        """Override to prevent service stop - this snap has no services."""
        logger.debug(
            "Skipping service stop - %s snap has no services", self.snap_name
        )
        pass


if __name__ == "__main__":  # pragma: nocover
    ops.main(EpaOrchestratorCharm)
