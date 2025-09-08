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

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

OVN_CHASSIS_PLUG = "ovn-chassis"
OVN_CHASSIS_SLOT = "microovn:ovn-chassis"


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
    _state = ops.framework.StoredState()

    def __init__(self, framework: ops.framework.Framework) -> None:
        super().__init__(framework)
        self._state.set_default(
            external_interface=None,
            bridge_name=None,
            physnet_name=None,
            enable_chassis_as_gw=None,
        )
        self.framework.observe(
            self.on.set_network_agents_local_settings_action,
            self._set_network_agents_local_settings_action,
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

    def _validated_network_config(self) -> tuple[str, str, str, bool]:
        """Validate and normalize network-related charm config.

        Returns: (iface, bridge, physnet, enable_gw)
        """
        iface = self._state.external_interface
        bridge = self._state.bridge_name
        physnet = self._state.physnet_name
        enable_gw = self._state.enable_chassis_as_gw
        enable_gw_bool = True if enable_gw is None else bool(enable_gw)

        missing = []
        if not iface:
            missing.append("external-interface")
        if not bridge:
            missing.append("bridge-name")
        if not physnet:
            missing.append("physnet-name")
        if enable_gw is None:
            missing.append("enable-chassis-as-gw")

        if missing:
            raise sunbeam_guard.BlockedExceptionError(
                f"missing: {', '.join(missing)}"
            )
        return str(iface), str(bridge), str(physnet), enable_gw_bool

    def _set_network_agents_local_settings_action(
        self, event: ops.ActionEvent
    ) -> None:
        """Action to set per-unit local settings for provider networking."""
        params = event.params or {}
        iface = params.get("external-interface")
        bridge = params.get("bridge-name")
        physnet = params.get("physnet-name")
        enable_gw = params.get("enable-chassis-as-gw")

        missing = [
            name
            for name, val in (
                ("external-interface", iface),
                ("bridge-name", bridge),
                ("physnet-name", physnet),
                ("enable-chassis-as-gw", enable_gw),
            )
            if val is None or val == ""
        ]
        if missing:
            event.fail(f"Missing required params: {', '.join(missing)}")
            return

        self._state.external_interface = str(iface)
        self._state.bridge_name = str(bridge)
        self._state.physnet_name = str(physnet)
        self._state.enable_chassis_as_gw = bool(enable_gw)

        try:
            self.configure_charm(event)
        except Exception as exc:
            event.fail(str(exc))
            return
        event.set_results(
            {
                "external-interface": self._state.external_interface,
                "bridge-name": self._state.bridge_name,
                "physnet-name": self._state.physnet_name,
                "enable-chassis-as-gw": self._state.enable_chassis_as_gw,
            }
        )

    def configure_snap(self, event: ops.EventBase) -> None:
        """Push configuration into the snap (no subprocess)."""
        self._connect_ovn_chassis()
        iface, bridge, physnet, enable_gw = self._validated_network_config()
        self.set_snap_data(
            {
                # consumed by the snap’s configure/bridge-setup helper
                "network.interface": iface,
                "network.bridge": bridge,
                "network.physnet": physnet,
                "network.enable-chassis-as-gw": enable_gw,
                "settings.debug": bool(
                    self.model.config.get("debug") or False
                ),
            }
        )


if __name__ == "__main__":  # pragma: nocover
    ops.main(OpenstackNetworkAgentsOperatorCharm)
