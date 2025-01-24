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

import requests
from zaza.openstack.charm_tests.keystone.setup import (
    wait_for_all_endpoints as zaza_wait_for_all_endpoints,
)

SERVICE_CODES = {
    "cinderv2": [requests.codes.not_found],
    # gnocchi is not started because no ceph relation
    "gnocchi": [requests.codes.bad_gateway],
    "heat-cfn": [requests.codes.bad_request],
    "masakari": [requests.codes.not_found],
}


def wait_for_all_endpoints(interface="public"):
    """Check all endpoints are returning an acceptable return code.

    :param interface: Endpoint type to check. public, admin or internal
    :type interface: str
    :raises: AssertionError
    """
    zaza_wait_for_all_endpoints(interface, SERVICE_CODES)
