#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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


"""Sunbeam Machine Charm.

This charm provide a place to add machine configuration and relate
subordinates that configure machine services.
"""

import logging

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
from ops.main import (
    main,
)
from charms.operator_libs_linux.v0 import (
    sysctl,
)

logger = logging.getLogger(__name__)


class SunbeamMachineCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "sunbeam-machine"

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self.framework.observe(self.on.remove, self._on_remove)
        self.sysctl = sysctl.Config(self.meta.name)

    def configure_unit(self, event: ops.EventBase):
        """Run configuration on this unit."""
        super().configure_unit(event)
        self._sysctl_configure()

    def _sysctl_configure(self):
        """Run sysctl configuration on the local machine."""
        sysctl_data = {"fs.inotify.max_user_instances": "1024"}
        try:
            self.sysctl.configure(config=sysctl_data)
        except (sysctl.ApplyError, sysctl.ValidationError):
            logger.error("Error setting values on sysctl", exc_info=True)
            raise sunbeam_guard.BlockedExceptionError("Sysctl config failed")
        except sysctl.CommandError:
            logger.error("Error executing sysctl", exc_info=True)
            raise sunbeam_guard.BlockedExceptionError("Sysctl command failed")

    def _on_remove(self, event: ops.RemoveEvent):
        self.sysctl.remove()


if __name__ == "__main__":  # pragma: nocover
    main(SunbeamMachineCharm)
