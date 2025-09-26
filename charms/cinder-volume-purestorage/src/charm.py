#!/usr/bin/env python3

#
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

"""Cinder purestorage Operator Charm.

This charm provide Cinder <-> purestorage integration as part
of an OpenStack deployment
"""

import ipaddress
import logging
from enum import (
    StrEnum,
)
from typing import (
    Annotated,
)

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic
from pydantic import (
    Field,
)

logger = logging.getLogger(__name__)


class NvmeTransport(StrEnum):
    """Enumeration of valid NVMe transport types."""

    # Only TCP is supported for now
    # ROCE = "roce"
    TCP = "tcp"


class Personality(StrEnum):
    """Enumeration of valid host personality types."""

    AIX = "aix"
    ESXI = "esxi"
    HITACHI_VSP = "hitachi-vsp"
    HPUX = "hpux"
    ORACLE_VM_SERVER = "oracle-vm-server"
    SOLARIS = "solaris"
    VMS = "vms"


ALL_NETWORK = ipaddress.ip_network("0.0.0.0/0")


def token_validator(value: ops.Secret) -> str:
    """Validate that the token is not empty."""
    if not isinstance(value, ops.Secret):
        raise ValueError("Token must be an ops.Secret instance")
    secret = value.get_content(refresh=True)
    token = secret.get("token")
    if token is None or not token.strip():
        raise ValueError(
            "API token secret must contain non-empty 'token' field"
        )
    return token


class PureStorageConfig(sunbeam_storage.CinderVolumeConfig):
    """Pydantic model for Pure Storage specific configuration options."""

    pure_api_token: Annotated[
        str, pydantic.BeforeValidator(token_validator)
    ] = Field(
        description="REST API authorization token from the Pure Storage FlashArray."
    )
    pure_automatic_max_oversubscription_ratio: bool = Field(
        default=True,
        description="Automatically determine an oversubscription ratio based on current total data reduction values.",
    )
    pure_eradicate_on_delete: bool = Field(
        default=False,
        description="When enabled, all Pure volumes, snapshots, and protection groups will be eradicated at deletion time.",
    )
    pure_host_personality: Personality | None = Field(
        default=None,
        description="Determines how the Purity system tunes the protocol used between the array and the initiator.",
    )
    pure_iscsi_cidr: pydantic.IPvAnyNetwork = Field(
        default=ALL_NETWORK,
        description="CIDR of FlashArray iSCSI targets hosts are allowed to connect to.",
    )
    pure_iscsi_cidr_list: list[pydantic.IPvAnyNetwork] | None = Field(
        default=None,
        description="Comma-separated list of CIDR of FlashArray iSCSI targets hosts are allowed to connect to.",
    )
    pure_nvme_cidr: pydantic.IPvAnyNetwork = Field(
        default=ALL_NETWORK,
        description="CIDR of FlashArray NVMe targets hosts are allowed to connect to.",
    )
    pure_nvme_cidr_list: str | None = Field(
        default=None,
        description="Comma-separated list of CIDR of FlashArray NVMe targets hosts are allowed to connect to.",
    )
    pure_nvme_transport: NvmeTransport = Field(
        default=NvmeTransport.TCP,
        description="NVMe transport layer to be used by the NVMe driver. Options: tcp",
    )
    pure_replica_interval_default: int = Field(
        default=3600,
        description="Snapshot replication interval in seconds. Default is 1 hour (3600).",
    )
    pure_replica_retention_long_term_default: int = Field(
        default=7,
        description="Retain snapshots per day on target for this time (in days). Default is 7 days.",
    )
    pure_replica_retention_long_term_per_day_default: int = Field(
        default=3,
        description="Retain how many snapshots for each day. Default is 3 snapshots per day.",
    )
    pure_replica_retention_short_term_default: int = Field(
        default=14400,
        description="Retain all snapshots on target for this time (in seconds). Default is 4 hours (14400).",
    )
    pure_replication_pg_name: str = Field(
        default="cinder-group",
        description="Pure Protection Group name to use for async replication.",
    )
    pure_replication_pod_name: str = Field(
        default="cinder-pod",
        description="Pure Pod name to use for sync replication.",
    )


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumePureStorageOperatorCharm(
    charm.OSCinderVolumeDriverOperatorCharm
):
    """Cinder/PureStorage Operator charm."""

    service_name = "cinder-volume-purestorage"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "pure." + self.model.app.name

    @property
    def configuration_class(self) -> type[PureStorageConfig]:
        """Return the configuration class."""
        return PureStorageConfig


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumePureStorageOperatorCharm)
