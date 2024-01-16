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

"""Clusterd client talking over unix socket."""

import json
import logging
from pathlib import (
    Path,
)
from urllib.parse import (
    quote,
)

import requests
import requests_unixsocket
from requests_unixsocket import (
    DEFAULT_SCHEME,
)

logger = logging.getLogger(__name__)


class ClusterdUnavailableError(Exception):
    """Raised when the cluster is unavailable."""


class ClusterdClient:
    """A client for interacting with the remote client API."""

    def __init__(self, socket_path: Path):
        self._socket_path = socket_path
        self._session = requests.sessions.Session()
        self._session.mount(
            requests_unixsocket.DEFAULT_SCHEME,
            requests_unixsocket.UnixAdapter(),
        )

    def _request(self, method: str, path: str, **kwargs) -> dict:
        if path.startswith("/"):
            path = path[1:]
        netloc = quote(str(self._socket_path), safe="")
        url = f"{DEFAULT_SCHEME}{netloc}/{path}"
        try:
            logging.debug("[%s] %s, args=%s", method, url, kwargs)
            response = self._session.request(method=method, url=url, **kwargs)
            logging.debug("Response(%s) = %s", response, response.text)
        except ConnectionError as e:
            msg = str(e)
            if "FileNotFoundError" in msg:
                raise ClusterdUnavailableError(
                    "Sunbeam Cluster socket not found, is clusterd running ?"
                    " Check with 'snap services openstack.clusterd'",
                ) from e
            raise ClusterdUnavailableError(msg)
        response.raise_for_status()
        return response.json()

    def _get(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", True)
        return self._request("get", path, **kwargs)

    def _post(self, path, data=None, json=None, **kwargs):
        return self._request("post", path, data=data, json=json, **kwargs)

    def _delete(self, path, **kwargs):
        return self._request("delete", path, **kwargs)

    def bootstrap(self, name: str, address: str):
        """Bootstrap clusterd."""
        data = {"bootstrap": True, "address": address, "name": name}
        self._post("/cluster/control", data=json.dumps(data))

    def join(self, name: str, address: str, token: str) -> None:
        """Join node to the micro cluster.

        Verified the token with the list of saved tokens and
        joins the node with the given name and address.
        """
        data = {"join_token": token, "address": address, "name": name}
        self._post("cluster/control", data=json.dumps(data))

    def get_node(self, name: str) -> dict:
        """Retrieve node information."""
        return self._get(f"/cluster/1.0/cluster/{name}")

    def remove_node(self, name: str):
        """Delete node."""
        self._delete(f"/cluster/1.0/cluster/{name}")

    def generate_token(self, name: str) -> str:
        """Generate token for the node.

        Generate a new token for the node with name.

        Raises TokenAlreadyGeneratedException if token is already
        generated.
        """
        data = {"name": name}
        result = self._post("/cluster/1.0/tokens", data=json.dumps(data))
        return str(result["metadata"])
