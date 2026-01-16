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

"""Cinder Dell PowerStore Operator Charm.

This charm provide Cinder <-> Dell PowerStore integration as part
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


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumePowerStoreOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Dell PowerStore Operator charm."""

    service_name = "cinder-volume-powerstore"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "powerstore." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()

        overrides.pop("protocol", None)

        overrides.update(
            {
                "san-ip": typing.Annotated[
                    str,
                    sunbeam_storage.Required,
                ],
                "san-login": typing.Annotated[
                    str,
                    sunbeam_storage.Required,
                ],
                "san-password": typing.Annotated[
                    str,
                    sunbeam_storage.Required,
                ],
                "storage-protocol": typing.Annotated[
                    typing.Literal["fc", "iscsi"],
                    pydantic.BeforeValidator(lambda v: None if v is None else str(v).lower()),
                    sunbeam_storage.Required,
                ],
                "protocol": typing.Annotated[
                    typing.Optional[typing.Literal["fc", "iscsi"]],
                    pydantic.BeforeValidator(lambda v: None if v is None else str(v).lower()),
                ],

#                "powerstore_nvme":,
#                "powerstore_ports":,
            }
        )
        return overrides
    
    def _resolve_protocol(self) -> str:
        cfg = self.model.config
        legacy = cfg.get("protocol")
        canon = cfg.get("storage-protocol")

        if canon:
            if legacy and legacy != canon:
                raise ValueError(
                    "Conflicting values: 'protocol' vs 'storage-protocol'"
                )
            return canon

        if legacy:
            return legacy

        #Default
        return "fc"


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumePowerStoreOperatorCharm)
