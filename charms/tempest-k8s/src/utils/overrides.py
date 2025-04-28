# Copyright 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tempest configuration overrides."""

from openstack import connection


def get_swift_overrides() -> str:
    """Return swift configuration override.

    Ceph reef supports SHA1 for hashing for tempurl access.
    SHA256 is used from v19.1.0
    Tempest 2024.1 uses SHA256.

    [1] https://github.com/ceph/ceph/commit/e2023d28dc6e6e835303716e7235df720d33a01c
    """
    return "object-storage-feature-enabled.tempurl_digest_hashlib sha1"


def get_external_net_override(credentials: dict, region: str) -> str:
    """Return 'public_network_id <uuid>' or ''."""
    conn = connection.Connection(
        auth_url=credentials.get("auth-url"),
        username=credentials.get("username"),
        password=credentials.get("password"),
        project_name=credentials.get("project-name"),
        user_domain_name=credentials.get("domain-name"),
        project_domain_name=credentials.get("domain-name"),
        region_name=region,
    )
    for net in conn.network.networks(is_router_external=True, status="ACTIVE"):
        if net.subnet_ids:
            return f"network.public_network_id {net.id}"
    return ""
