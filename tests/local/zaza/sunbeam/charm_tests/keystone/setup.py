# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import subprocess

import requests
import zaza.openstack.utilities.openstack as openstack_utils
from zaza.openstack.charm_tests.keystone.setup import (
    wait_for_all_endpoints as zaza_wait_for_all_endpoints,
)

SERVICE_CODES = {
    "heat-cfn": [requests.codes.bad_request],
    "heat": [requests.codes.bad_request],
}


def wait_for_all_endpoints(interface="internal"):
    """Check all endpoints are returning an acceptable return code.

    :param interface: Endpoint type to check. public, admin or internal
    :type interface: str
    :raises: AssertionError
    """
    zaza_wait_for_all_endpoints(interface, SERVICE_CODES)


def wait_for_all_endpoints_debug(interface="internal", service_codes=None):
    """Check all endpoints are returning an acceptable return code.

    :param interface: Endpoint type to check. public, admin or internal
    :type interface: str
    :param service_codes: Dict of service names and acceptable return codes
    :type service_codes: Optional[dict]
    :raises: AssertionError
    """
    if service_codes is None:
        service_codes = {}

    import zaza.model as model

    action = model.run_action_on_leader(
        "traefik",
        "show-proxied-endpoints",
    )
    logging.warning("result: %r", action.data)

    keystone_client = openstack_utils.get_keystone_overcloud_session_client()
    curl = ["curl", "-v", "-k", "-i"]
    for service in keystone_client.services.list():
        for ep in keystone_client.endpoints.list(
            service=service, interface=interface
        ):
            subprocess.run(curl + [ep.url])
