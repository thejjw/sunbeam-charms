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

"""Enhanced Platform Awareness (EPA) client."""

import json
import logging
import os
import socket

DEFAULT_EPA_SOCK_PATH = "/var/snap/epa-orchestrator/current/data/epa.sock"
DEFAULT_EPA_VERSION = "1.0"


class EPAError(Exception):
    """EPA service exception."""

    pass


class EPAConnectionError(EPAError):
    """EPA service connection failure."""

    pass


class EPAClient:
    """Enhanced Platform Awareness (EPA) service client.

    Used to reserve resources such as cpu cores or huge pages.
    """

    def __init__(self, socket_path: str = DEFAULT_EPA_SOCK_PATH):
        self._socket_path = socket_path

    def is_available(self) -> bool:
        """Check if EPA service is available.

        Returns:
            True if the socket exists, False otherwise.
        """
        return os.path.exists(self._socket_path)

    def _send_request(self, request: dict) -> dict:
        logging.debug("EPA request: %s", request)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            try:
                sock.connect(self._socket_path)
            except Exception as ex:
                raise EPAConnectionError(
                    f"Unable to connect to EPA service socket: {self._socket_path}. "
                    f"Error: {ex}."
                )
            sock.sendall(json.dumps(request).encode())
            reply_json = sock.recv(4096).decode()
            logging.debug("EPA reply: %s", reply_json)

        reply = json.loads(reply_json)
        error = reply.get("error")
        if error:
            raise EPAError(
                f"EPA request failed. Error: {error}, request: {request}"
            )

        return reply

    def _parse_epa_core_list(self, core_list_str: str) -> list[int]:
        """Parse core lists returned by the EPA service.

        "" -> []
        "1,2,3" -> [1, 2, 3]
        "1-4,9-12" -> [1, 2, 3, 4, 9, 10, 11, 12]
        """
        cores: list[int] = []
        for core in core_list_str.split(","):
            if not core:
                continue
            if "-" in core:
                start_str, end_str = core.split("-")
                cores += list(range(int(start_str), int(end_str) + 1))
            else:
                cores.append(int(core))
        return cores

    def allocate_cores(
        self,
        service_name: str,
        core_count: int,
        numa_node: int | None = None,
    ) -> list[int]:
        """Reserve the specified amount of cores.

        Accepts an optional NUMA node.
        Specify "-1" to remove allocations from this NUMA node.
        """
        request = {
            "version": DEFAULT_EPA_VERSION,
            "service_name": service_name,
            "action": "allocate_cores",
            "num_of_cores": core_count,
        }
        if numa_node is not None:
            request["numa_node"] = numa_node
            request["action"] = "allocate_numa_cores"
        response = self._send_request(request)

        return self._parse_epa_core_list(response["cores_allocated"])

    def allocate_hugepages(
        self,
        service_name: str,
        hugepages_requested: int,
        hugepage_size_kb: int,
        numa_node: int,
    ):
        """Reserve huge pages.

        Request "-1" hugepages to remove allocations from this NUMA node.
        """
        request = {
            "version": DEFAULT_EPA_VERSION,
            "service_name": service_name,
            "action": "allocate_hugepages",
            "hugepages_requested": hugepages_requested,
            "size_kb": hugepage_size_kb,
            "node_id": numa_node,
        }
        self._send_request(request)
