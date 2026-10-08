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
import typing

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic

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


class ReplicationType(StrEnum):
    """Enumeration of possible replication types."""

    ASYNC = "async"
    SYNC = "sync"


def ip_network_list_validator(value: str) -> list[pydantic.IPvAnyNetwork]:
    """Validate and parse a comma-separated list of IP networks."""
    if not value:
        raise ValueError("Value cannot be empty")
    try:
        return [ipaddress.ip_network(ip.strip()) for ip in value.split(",")]
    except ValueError as e:
        raise ValueError(f"Invalid IP network: {e}")


def list_serializer(value: list) -> str:
    """Serialize a list to a comma-separated string."""
    return ",".join(str(v) for v in value)


CIDR_LIST_TYPING = typing.Annotated[
    list[pydantic.IPvAnyNetwork] | None,
    pydantic.BeforeValidator(ip_network_list_validator),
    pydantic.PlainSerializer(list_serializer, return_type=str),
]


REPLICATION_GROUP = sunbeam_storage.RequiredIfGroup("replication")
_REPLICATION_KEYS = (
    "replication-target-name",
    "replication-target-address",
    "replication-target-api-token",
    "replication-type",
    "replication-sync-uniform",
)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumePureStorageOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/PureStorage Operator charm."""

    service_name = "cinder-volume-purestorage"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "pure." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()
        overrides.update(
            {
                "pure-api-token": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("token")),
                    sunbeam_storage.Required,
                ],
                "pure-host-personality": Personality | None,
                "pure-iscsi-cidr": pydantic.IPvAnyNetwork | None,
                "pure-iscsi-cidr-list": CIDR_LIST_TYPING,
                "pure-nvme-cidr": pydantic.IPvAnyNetwork | None,
                "pure-nvme-cidr-list": CIDR_LIST_TYPING,
                "pure-nvme-transport": NvmeTransport,
                "replication-target-name": typing.Annotated[ 
                    str | None, REPLICATION_GROUP
                ],
                "replication-target-address": typing.Annotated[ 
                    pydantic.IPvAnyAddress | str | None, REPLICATION_GROUP
                ],
                "replication-target-api-token": typing.Annotated[ 
                    str | None,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("token")),
                    REPLICATION_GROUP,
                ],
                "replication-type": ReplicationType,
            }
        )
        return overrides


    def get_backend_configuration(self) -> typing.Mapping:
        """Fold replication target options into replication-device."""
        config = dict(super().get_backend_configuration())
        target = {k: config.pop(k, None) for k in _REPLICATION_KEYS}
        if target["replication-target-name"]:
            device = (
                f"backend_id:{target['replication-target-name']},"
                f"san_ip:{target['replication-target-address']},"
                f"api_token:{target['replication-target-api-token']},"
                f"type:{target['replication-type']}"
            )
            if (
                target["replication-type"] == ReplicationType.SYNC
                and target["replication-sync-uniform"]
            ):
                device += ",uniform:true"
            config["replication-device"] = device
        return config




if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumePureStorageOperatorCharm)
