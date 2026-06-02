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

"""Cinder dellunity Operator Charm.

This charm provide Cinder <-> dellunity integration as part
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


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    ISCSI = "iscsi"
    FC = "fc"


def ip_network_list_validator(value: str) -> list[pydantic.IPvAnyNetwork]:
    if not value:
        raise ValueError("Value cannot be empty")
    try:
        return [ipaddress.ip_network(ip.strip()) for ip in value.split(",")]
    except ValueError as e:
        raise ValueError(f"Invalid IP network: {e}")


def list_serializer(value: list) -> str:
    return ",".join(str(v) for v in value)


CIDR_LIST_TYPING = typing.Annotated[
    list[pydantic.IPvAnyNetwork] | None,
    pydantic.BeforeValidator(ip_network_list_validator),
    pydantic.PlainSerializer(list_serializer, return_type=str),
]


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeDellunityOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/DellUnity Operator charm."""

    service_name = "cinder-volume-dellunity"

    @property
    def backend_key(self) -> str:
        return "dellunity." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        overrides = super()._configuration_type_overrides()
        overrides.pop("driver-ssl-cert", None)
        overrides.update(
            {
                "protocol": typing.Annotated[
                    typing.Literal["iscsi", "fc"], sunbeam_storage.Required
                ],
                "san-login": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("san-login")),
                    sunbeam_storage.Required,
                ],
                "san-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("san-password")),
                    sunbeam_storage.Required,
                ],
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeDellunityOperatorCharm)
