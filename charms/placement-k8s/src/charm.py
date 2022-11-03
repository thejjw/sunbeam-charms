#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd.
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


"""Placement Operator Charm.

This charm provide Placement services as part of an OpenStack deployment
"""

import logging

import ops_sunbeam.charm as sunbeam_charm
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)


class PlacementOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "placement-api"
    wsgi_admin_script = "/usr/bin/placement-api"
    wsgi_public_script = "/usr/bin/placement-api"

    db_sync_cmds = [
        ["sudo", "-u", "placement", "placement-manage", "db", "sync"]
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/placement/placement.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "placement"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "placement"

    @property
    def service_endpoints(self):
        """Service endpoints description."""
        return [
            {
                "service_name": "placement",
                "type": "placement",
                "description": "OpenStack Placement API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default ingress port."""
        return 8778


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(PlacementOperatorCharm, use_juju_for_storage=True)
