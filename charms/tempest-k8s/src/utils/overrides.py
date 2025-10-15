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

import logging
from typing import (
    List,
    Set,
)

from ops_sunbeam.guard import (
    BlockedExceptionError,
)

logger = logging.getLogger(__name__)


def get_swift_overrides() -> str:
    """Return swift configuration override.

    Ceph reef supports SHA1 for hashing for tempurl access.
    SHA256 is used from v19.1.0
    Tempest 2024.1 uses SHA256.

    [1] https://github.com/ceph/ceph/commit/e2023d28dc6e6e835303716e7235df720d33a01c
    """
    return "object-storage-feature-enabled.tempurl_digest_hashlib sha1"


def get_compute_overrides() -> str:
    """Return compute configuration override."""
    return " ".join(
        (
            "compute-feature-enabled.resize false",  # lp:2082056
            "compute-feature-enabled.cold_migration false",  # lp:2082056
        )
    )


def get_ironic_overrides() -> str:
    """Return ironic configuration override."""
    return " ".join(("baremetal.endpoint_type public",))


def get_manila_overrides() -> str:
    """Return manila configuration override."""
    return " ".join(
        (
            "share.catalog_type sharev2",
            "share.endpoint_type public",
            "share.capability_storage_protocol NFS",
        )
    )


def get_role_based_overrides(config_roles: str) -> str:
    """Generate tempest.conf overrides based on the configured roles.

    :param configured_roles: A set of role strings, e.g., {"compute", "storage"}.
    :return: A string of space-separated key-value pairs for overrides.
    """
    configured_roles = _parse_roles_config(config_roles)
    overrides: List[str] = []

    if "storage" not in configured_roles:
        logger.info("Storage role not configured, disabling cinder tests.")
        overrides.extend(["service_available.cinder", "false"])

    if "compute" not in configured_roles:
        logger.info("Compute role not configured, disabling nova tests.")
        overrides.extend(["service_available.nova", "false"])

    return " ".join(overrides)


def _parse_roles_config(config_roles: str) -> Set[str]:
    """Parses the 'roles' config string from the charm's configuration.

    :param config_roles: The charm's config object (self.config in the charm).
    :return: A set of lower-case role strings.
    """
    if not config_roles.strip():
        error_message = (
            "Config option 'roles' must contain at least one role "
            "(compute, control, storage)."
        )
        logger.error(error_message)
        raise BlockedExceptionError(error_message)

    parsed_user_roles = {
        role.strip().lower()
        for role in config_roles.split(",")
        if role.strip()
    }

    valid_roles = {"compute", "control", "storage"}

    invalid_user_roles = parsed_user_roles - valid_roles
    if invalid_user_roles:
        error_message = (
            f"Invalid roles specified in 'roles' config: {', '.join(invalid_user_roles)}. "
            f"Valid roles are: {', '.join(valid_roles)}."
        )
        logger.error(error_message)
        raise BlockedExceptionError(error_message)

    return parsed_user_roles
