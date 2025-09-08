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


import subprocess

from ops.charm import (
    CharmBase,
)
from ops.main import (
    main,
)
from ops.model import (
    ActiveStatus,
    BlockedStatus,
)

SNAP = "openstack-network-agents"


class OpenstackNetworkAgentsCharm(CharmBase):
    """Minimal charm: set OVS bridge mapping + optional chassis-as-gw via the snap."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.config_changed, self._on_changed)

    def _install_snap(self) -> None:
        subprocess.run(
            ["snap", "install", SNAP],
            check=True,
            capture_output=True,
            text=True,
        )

    def _post_install_connects(self) -> None:
        subprocess.run(
            ["snap", "connect", f"{SNAP}:network-control", ":network-control"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["snap", "connect", f"{SNAP}:network", ":network"],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["snap", "connect", f"{SNAP}:ovn-chassis", "microovn:ovn-chassis"],
            check=True,
            capture_output=True,
            text=True,
        )

    def _apply_bridge_mapping(
        self, iface: str, bridge: str, physnet: str, enable_gw: bool
    ) -> None:
        subprocess.run(
            [
                "snap",
                "set",
                SNAP,
                f"network.interface={iface}",
                f"network.bridge={bridge}",
                f"network.physnet={physnet}",
                f"network.enable-chassis-as-gw={str(bool(enable_gw)).lower()}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["snap", "run", f"{SNAP}.bridge-setup", "apply-from-snap-config"],
            check=True,
            capture_output=True,
            text=True,
        )

    def _on_install(self, _):
        self._install_snap()
        self._post_install_connects()
        self.unit.status = ActiveStatus("installed")

    def _on_changed(self, _):
        iface = self.config.get("external-interface")
        bridge = self.config.get("bridge-name")
        phys = self.config.get("physnet-name")
        enable_gw = self.config.get("enable-chassis-as-gw") or True

        missing = []
        if not iface:
            missing.append("external-interface")
        if not bridge:
            missing.append("bridge-name")
        if not phys:
            missing.append("physnet-name")

        if missing:
            self.unit.status = BlockedStatus("missing: " + ", ".join(missing))
            return

        try:
            self._apply_bridge_mapping(iface, bridge, phys, enable_gw)
        except subprocess.CalledProcessError as e:
            msg = (e.stderr or e.stdout or "").strip()
            self.unit.status = BlockedStatus(f"bridge setup failed: {msg}")
            return

        self.unit.status = ActiveStatus("ovs bridge mapping configured")


if __name__ == "__main__":
    main(OpenstackNetworkAgentsCharm)
