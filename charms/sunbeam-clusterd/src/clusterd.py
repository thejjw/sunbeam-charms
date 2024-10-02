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
        except requests.exceptions.ConnectionError as e:
            msg = str(e)
            if "FileNotFoundError" in msg:
                raise ClusterdUnavailableError(
                    "Sunbeam Cluster socket not found, is clusterd running ?"
                    " Check with 'snap services openstack.clusterd'",
                ) from e
            raise ClusterdUnavailableError(msg)
        except requests.exceptions.HTTPError as e:
            if e.response is not None:
                logger.debug(
                    f"HTTPError: {e.response.status_code}, {e.response.text}"
                )
                if e.response.status_code == 503:
                    raise ClusterdUnavailableError(str(e)) from e
            raise e
        response.raise_for_status()
        return response.json()

    def _get(self, path, **kwargs):
        kwargs.setdefault("allow_redirects", True)
        return self._request("get", path, **kwargs)

    def _post(self, path, data=None, json=None, **kwargs):
        return self._request("post", path, data=data, json=json, **kwargs)

    def _put(self, path, data=None, json=None, **kwargs):
        return self._request("put", path, data=data, json=json, **kwargs)

    def _delete(self, path, **kwargs):
        return self._request("delete", path, **kwargs)

    def ready(self) -> bool:
        """Is the cluster ready."""
        try:
            self._get("/core/1.0/ready")
        except ClusterdUnavailableError:
            return False
        return True

    def shutdown(self):
        """Shutdown local clusterd."""
        try:
            self._post("/core/control/shutdown")
        except requests.exceptions.HTTPError as e:
            if e.response is None:
                raise e
            is_500 = e.response.status_code == 500
            is_closed_anyway = (
                "but connection was closed anyway" in e.response.text
            )
            if is_500 and is_closed_anyway:
                logger.debug("Clusterd shutdown")
                return
            raise e

    def bootstrap(self, name: str, address: str):
        """Bootstrap clusterd."""
        data = {"bootstrap": True, "address": address, "name": name}
        self._post("/core/control", data=json.dumps(data))

    def join(self, name: str, address: str, token: str) -> None:
        """Join node to the micro cluster.

        Verified the token with the list of saved tokens and
        joins the node with the given name and address.
        """
        data = {"join_token": token, "address": address, "name": name}
        self._post("core/control", data=json.dumps(data))

    def get_members(self) -> list[dict]:
        """Get cluster members."""
        cluster = self._get("/core/1.0/cluster")["metadata"]
        return cluster

    def get_member(self, name) -> dict:
        """Get cluster member."""
        for member in self.get_members():
            if member["name"] == name:
                return member
        raise ValueError(f"Member {name} not found")

    def remove_node(
        self,
        name: str,
        force: bool = False,
        allow_not_found: bool = True,
    ):
        """Delete node."""
        int_force = 1 if force else 0
        try:
            self._delete(f"/core/1.0/cluster/{name}?force={int_force}")
        except requests.exceptions.HTTPError as e:
            if e.response is None:
                raise e
            if e.response.status_code == 404 and allow_not_found:
                logger.debug(f"Node {name} not found")
                return
            is_500 = e.response.status_code == 500
            remote_not_found = is_500 and (
                "No remote exists with the given name" in e.response.text
            )
            no_dqlite_member = (
                is_500
                and "No dqlite cluster member exists with the given name"
                in e.response.text
            )
            delete_with_url = (
                is_500 and f"cluster/1.0/cluster/{name}" in e.response.text
            )
            not_found = remote_not_found or no_dqlite_member or delete_with_url
            if not_found and allow_not_found:
                logger.debug(f"Node {name} not found")
                return
            raise e

    def generate_token(self, name: str) -> str:
        """Generate token for the node.

        Generate a new token for the node with name.
        """
        data = {"name": name}
        result = self._post("/core/control/tokens", data=json.dumps(data))
        return str(result["metadata"])

    def set_certs(self, ca: str, cert: str, key: str):
        """Configure cluster certificates.

        The CA is not set in the cluster certificates, but in the config endpoint.
        This is because we don't want microcluster to go full CA-mode.
        """
        self._put("/1.0/config/cluster-ca", data=ca)
        data = {"cert": cert, "key": key}
        self._put("/core/1.0/cluster/certificates/cluster", json=data)
