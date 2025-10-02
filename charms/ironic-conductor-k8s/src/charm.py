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

import hashlib
import logging
import uuid
from typing import (
    List,
    Mapping,
)

import api_utils
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

    @property
    def directories(self) -> list[sunbeam_chandlers.ContainerDir]:
        """List of directories to create in container."""
        return [
            sunbeam_chandlers.ContainerDir(
                "/tftpboot",
                self.charm.service_user,
                self.charm.service_group,
            ),
            sunbeam_chandlers.ContainerDir(
                "/httpboot",
                self.charm.service_user,
                self.charm.service_group,
            ),
        ]

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

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run the constructor."""
        super().__init__(framework)
        self.framework.observe(
            self.on.set_temp_url_secret_action,
            self._on_set_temp_url_secret_action,
        )

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
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/rootwrap.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/tftpboot/map-file",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/tftpboot/grub/grub.cfg",
                self.service_user,
                self.service_group,
                0o640,
            ),
        ]
        return _cconfigs

    def _on_set_temp_url_secret_action(
        self, event: ops.charm.ActionEvent
    ) -> None:
        """Run set-temp-url-secret action."""
        if not self.model.unit.is_leader():
            event.fail("action must be run on the leader unit.")
            return

        if self.get_mandatory_relations_not_ready(event):
            event.fail(
                "required relations are not yet available, please defer action until "
                "deployment is complete."
            )
            return

        try:
            keystone_session = api_utils.create_keystone_session(
                self.ccreds.interface
            )
        except Exception as e:
            event.fail(f"failed to create keystone session ('{e}')")
            return

        os_cli = api_utils.OSClients(keystone_session)
        if not os_cli.has_swift():
            event.fail(
                "Swift not yet available. Please wait for deployment to finish"
            )
            return

        if not os_cli.has_glance():
            event.fail(
                "Glance not yet available. Please wait for deployment to finish"
            )
            return

        if "swift" not in os_cli.glance_stores:
            event.fail(
                "Glance does not support Swift storage backend. "
                "Please add relation between glance and microceph-ceph-rgw/swift"
            )
            return

        current_secret = self.leader_get("temp_url_secret")
        current_swift_secret = os_cli.get_object_account_properties().get(
            "temp-url-key", None
        )
        if current_secret and current_swift_secret == current_secret:
            # Already stored.
            event.set_results({"output": "Temp URL secret set."})
            return

        # Generate a secret and store it.
        secret = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
        os_cli.set_object_account_property("temp-url-key", secret)
        self.leader_set({"temp_url_secret": secret})

        event.set_results({"output": "Temp URL secret set."})


if __name__ == "__main__":  # pragma: nocover
    ops.main(IronicConductorOperatorCharm)
