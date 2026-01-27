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

import logging
import typing
from typing import Optional, List

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeDellPowerStoreOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Dell PowerStore Operator charm."""

    service_name = "cinder-volume-dellpowerstore"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "dellpowerstore." + self.model.app.name

    def _parse_ports(self, raw_ports: Optional[str]) -> list[str]:
        """Convert comma-separated ports into a clean list.

        Handles empty strings, None, spaces, and stray commas.
        Example inputs:
          - "" -> []
          - "10.0.0.10" -> ["10.0.0.10"]
          - "10.0.0.10,10.0.0.11" -> ["10.0.0.10", "10.0.0.11"]
          - "20:00:..., 20:00:..." -> ["20:00:...", "20:00:..."]
        """
        if not raw_ports:
            return []

        return [p.strip() for p in raw_ports.split(",") if p.strip()]


    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()

        overrides.update(
            {
                "san-ip": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("san-ip")
                    ),
                    sunbeam_storage.Required,
                ],
                "san-login": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("san-login")
                    ),
                    sunbeam_storage.Required,
                ],
                "san-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("san-password")
                    ),
                    sunbeam_storage.Required,
                ],
                "protocol": typing.Annotated[
                    typing.Optional[typing.Literal["fc", "iscsi"]],
                    pydantic.BeforeValidator(lambda v: None if v is None else str(v).lower()),
                ],
                "powerstore_nvme": typing.Annotated[
                    Optional[bool],
                ],
                "powerstore_ports": typing.Annotated[
                    Optional[list[str]],
                    pydantic.BeforeValidator(self._parse_ports),
                ],
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeDellPowerStoreOperatorCharm)
