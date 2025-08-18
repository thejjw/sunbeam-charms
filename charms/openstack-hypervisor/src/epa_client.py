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
import socket

DEFAULT_EPA_SOCK_PATH = "/var/snap/epa-orchestrator/current/data/epa.sock"
DEFAULT_EPA_VERSION = "1.0"

# Used to register OVS EPA reservations.
EPA_SERVICE_OVS = "ovs"


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

    def _connect(self):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.connect(self._socket_path)
            return sock
        except Exception as ex:
            raise EPAConnectionError(
                f"Unable to connect to EPA service socket: {self._socket_path}. "
                f"Error: {ex}."
            )

    def _send_request(self, request: dict) -> dict:
        logging.debug("EPA request: %s", request)

        sock = self._connect()
        sock.sendall(json.dumps(request).encode())

        reply = sock.recv(4096).decode()
        logging.debug("EPA reply: %s", reply)

        # Unfortunately not all requests include this field in case of failures.
        error = reply.get("error")
        if error:
            raise EPAError(
                f"EPA request failed. Error: {error}, request: {request}"
            )

        return reply

    def explicitly_allocate_cores(
        self,
        service_name: str,
        core_list: list[int],
    ):
        """Reserve the specified cores."""
        request = {
            "version": DEFAULT_EPA_VERSION,
            "service_name": service_name,
            "action": "explicitly_allocate_cores",
            "cores_requested": ",".join([str(core) for core in core_list]),
        }
        reply = self._send_request(request)
        rejected_cores = reply.get("cores_rejected")
        if rejected_cores:
            raise EPAError(
                "Unable to reserve cores: {rejected_cores}. "
                "Response: {reply}."
            )

    def allocate_hugepages(
        self,
        service_name: str,
        hugepages_requested: int,
        hugepage_size_kb: int,
        numa_node: int,
    ):
        """Reserve huge pages."""
        request = {
            "version": DEFAULT_EPA_VERSION,
            "service_name": service_name,
            "action": "allocate_hugepages",
            "hugepages_requested": hugepages_requested,
            "size_kb": hugepage_size_kb,
            "node_id": numa_node,
        }
        reply = self._send_request(request)

        # TODO: use common error reporting if that change is made in EPA.
        # We shouldn't have to use different error field for each request.
        succeeded = reply.get("allocation_successful", True)
        if not succeeded:
            raise EPAError(
                "Unable to reserve huge pages. Message: %s"
                % reply.get("message")
            )
