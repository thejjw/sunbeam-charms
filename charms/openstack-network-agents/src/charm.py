#!/usr/bin/env python3

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

"""Openstack Network Agents subordinate charm.

This charm deploys the `openstack-network-agents` snap and configures
OVS bridge mapping + optional chassis-as-gw on the network role node.
"""

from __future__ import (
    annotations,
)

import logging
import subprocess

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

OVN_CHASSIS_PLUG = "ovn-chassis"
OVN_CHASSIS_SLOT = "microovn:ovn-chassis"


class AgentError(Exception):
    """Custom exception for Agent errors."""


@sunbeam_tracing.trace_type
class JujuInfoRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for juju-info interface."""

    def setup_event_handler(self):
        """Set up event handler."""
        rel = self.charm.on[self.relation_name]
        self.framework.observe(rel.relation_joined, self._on_event)
        self.framework.observe(rel.relation_changed, self._on_event)
        self.framework.observe(rel.relation_broken, self._on_event)
        return None

    def _on_event(self, event: ops.RelationEvent) -> None:
        """Handle juju-info relation events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """True if at least one juju-info relation is present."""
        return bool(self.charm.model.relations.get(self.relation_name))


@sunbeam_tracing.trace_sunbeam_charm
class OpenstackNetworkAgentsOperatorCharm(
    sunbeam_charm.OSBaseOperatorCharmSnap
):
    """Snap-based subordinate for OVN provider bridge configuration (no daemons)."""

    service_name = "openstack-network-agents"

    def __init__(self, framework: ops.framework.Framework) -> None:
        super().__init__(framework)
        self.framework.observe(
            self.on.set_network_agents_local_settings_action,
            self._set_network_agents_local_settings_action,
        )
        self.framework.observe(
            self.on.list_nics_action,
            self._list_nics_action,
        )

    @property
    def snap_name(self) -> str:
        """Snap to install (configurable for dev/testing)."""
        return str(self.model.config.get("snap-name"))

    @property
    def snap_channel(self) -> str:
        """Channel to track in the Snap Store."""
        return str(self.model.config.get("snap-channel"))

    def ensure_services_running(self, enable: bool = True) -> None:
        """No-op; there are no services to start."""
        pass

    def stop_services(self, relation: set[str] | None = None) -> None:
        """No-op; there are no services to stop."""
        pass

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Return a list of relation handlers used by this charm."""
        handlers = handlers or []
        if self.can_add_handler("juju-info", handlers):
            juju_info = JujuInfoRequiresHandler(
                self,
                "juju-info",
                self.configure_charm,
                "juju-info" in self.mandatory_relations,
            )
            handlers.append(juju_info)
        return super().get_relation_handlers(handlers)

    def _connect_ovn_chassis(self) -> None:
        """Connect the snap ovn-chassis plug to microovn:ovn-chassis."""
        openstack_network_agents = self.get_snap()

        try:
            openstack_network_agents.connect(
                OVN_CHASSIS_PLUG, slot=OVN_CHASSIS_SLOT
            )
            logger.info(
                "Connected microovn:ovn-chassis slot to openstack-network-agents:ovn-chassis plug"
            )
        except snap.SnapError as e:
            logger.error(
                f"Failed to connect to microovn:ovn-chassis: {e.message}"
            )
            raise

    def _set_network_agents_local_settings_action(
        self, event: ops.ActionEvent
    ) -> None:
        """Action to set per-unit local settings for provider networking."""
        local_settings = [
            "network.external-interface",
            "network.bridge-name",
            "network.physnet-name",
            "network.bridge-mapping",
            "network.enable-chassis-as-gw",
        ]

        new_snap_settings = {}
        for setting in local_settings:
            action_param = setting.split(".")[1]
            if event.params.get(action_param):
                new_snap_settings[setting] = event.params.get(action_param)
        if new_snap_settings:
            self.set_snap_data(new_snap_settings)

    def configure_snap(self, event: ops.EventBase) -> None:
        """Push configuration into the snap (no subprocess)."""
        self._connect_ovn_chassis()
        self.set_snap_data(
            {
                "settings.debug": bool(
                    self.model.config.get("debug") or False
                ),
            }
        )

    def _agent_cli_cmd(self, cmd: str):
        """Helper to run cli commands on the snap."""
        process = subprocess.run(
            [
                "snap",
                "run",
                self.snap_name,
                "--verbose",
            ]
            + cmd.split(),
            capture_output=True,
        )

        stderr = process.stderr.decode("utf-8")
        logger.debug("logs: %s", stderr)
        stdout = process.stdout.decode("utf-8")
        logger.debug("stdout: %s", stdout)
        if process.returncode != 0:
            raise AgentError(stderr)

        return stdout

    def _list_nics_action(self, event: ops.ActionEvent):
        """Run list_nics action."""
        try:
            stdout = self._agent_cli_cmd("list-nics --format json")
        except AgentError as e:
            event.fail(str(e))
            return

        # cli returns a json dict with keys "nics" and "candidate"
        event.set_results({"result": stdout})


if __name__ == "__main__":  # pragma: nocover
    ops.main(OpenstackNetworkAgentsOperatorCharm)
