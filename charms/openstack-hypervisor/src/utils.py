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

"""Utility helper functions."""


import collections
import logging
import os
import subprocess
from typing import (
    Optional,
)

import netifaces

logger = logging.getLogger(__name__)


def _get_default_gw_iface_fallback() -> Optional[str]:
    """Returns the default gateway interface.

    Parses the /proc/net/route table to determine the interface with a default
    route. The interface with the default route will have a destination of 0x000000,
    a mask of 0x000000 and will have flags indicating RTF_GATEWAY and RTF_UP.

    :return Optional[str, None]: the name of the interface the default gateway or
            None if one cannot be found.
    """
    # see include/uapi/linux/route.h in kernel source for more explanation
    RTF_UP = 0x1  # noqa - route is usable
    RTF_GATEWAY = 0x2  # noqa - destination is a gateway

    iface = None
    with open("/proc/net/route", "r") as f:
        contents = [line.strip() for line in f.readlines() if line.strip()]
        logger.debug(contents)

        entries = []
        # First line is a header line of the table contents. Note, we skip blank entries
        # by default there's an extra column due to an extra \t character for the table
        # contents to line up. This is parsing the /proc/net/route and creating a set of
        # entries. Each entry is a dict where the keys are table header and the values
        # are the values in the table rows.
        header = [
            col.strip().lower() for col in contents[0].split("\t") if col
        ]
        for row in contents[1:]:
            cells = [col.strip() for col in row.split("\t") if col]
            entries.append(dict(zip(header, cells)))

        def is_up(flags: str) -> bool:
            return int(flags, 16) & RTF_UP == RTF_UP

        def is_gateway(flags: str) -> bool:
            return int(flags, 16) & RTF_GATEWAY == RTF_GATEWAY

        # Check each entry to see if it has the default gateway. The default gateway
        # will have destination and mask set to 0x00, will be up and is noted as a
        # gateway.
        for entry in entries:
            if int(entry.get("destination", 0xFF), 16) != 0:
                continue
            if int(entry.get("mask", 0xFF), 16) != 0:
                continue
            flags = entry.get("flags", 0x00)
            if is_up(flags) and is_gateway(flags):
                iface = entry.get("iface", None)
                break

    return iface


def get_ifaddresses_by_default_route() -> dict:
    """Get address configuration from interface associated with default gateway."""
    interface = "lo"
    ip = "127.0.0.1"
    netmask = "255.0.0.0"

    # TOCHK: Gathering only IPv4
    default_gateways = netifaces.gateways().get("default", {})
    if default_gateways and netifaces.AF_INET in default_gateways:
        interface = netifaces.gateways()["default"][netifaces.AF_INET][1]
    else:
        # There are some cases where netifaces doesn't return the machine's default
        # gateway, but it does exist. Let's check the /proc/net/route table to see
        # if we can find the proper gateway.
        interface = _get_default_gw_iface_fallback() or "lo"

    ip_list = netifaces.ifaddresses(interface)[netifaces.AF_INET]
    if len(ip_list) > 0 and "addr" in ip_list[0]:
        return ip_list[0]

    return {"addr": ip, "netmask": netmask}


def get_local_ip_by_default_route() -> str:
    """Get IP address of host associated with default gateway."""
    return get_ifaddresses_by_default_route()["addr"]


def get_interface_pci_address(ifname: str) -> str:
    """Determine the interface PCI address.

    :param: ifname: interface name
    :type: str
    :returns: the PCI address of the device.
    :rtype: str
    """
    net_dev_path = f"/sys/class/net/{ifname}/device"
    if not (os.path.exists(net_dev_path) and os.path.islink(net_dev_path)):
        # Not a PCI device.
        return ""
    resolved_path = os.path.realpath(net_dev_path)
    parts = resolved_path.split("/")
    if "virtio" in parts[-1]:
        return parts[-2]
    return parts[-1]


def get_pci_numa_node(pci_address: str) -> int | None:
    """Determine the NUMA node of a given PCI device."""
    device_path = f"/sys/bus/pci/devices/{pci_address}"
    numa_node_path = os.path.join(device_path, "numa_node")

    if not os.path.exists(device_path):
        raise ValueError("PCI device not found: %s" % pci_address)

    if not os.path.exists(numa_node_path):
        # No associated NUMA node.
        return None

    with open(numa_node_path, "r") as f:
        return int(f.read().strip())


def get_cpu_numa_architecture() -> dict:
    """Returns a mapping containing the list of cpu cores for each numa node."""
    result = subprocess.run(
        ["lscpu", "-p=cpu,node"], capture_output=True, check=True, text=True
    )

    numa_nodes = collections.defaultdict(list)
    for line in result.stdout.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        core_id, numa_node_id = line.split(",")
        numa_nodes[int(numa_node_id)].append(int(core_id))

    return dict(numa_nodes)
