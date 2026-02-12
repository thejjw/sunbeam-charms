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

"""Cinder Dell SC Operator Charm.

This charm provides Cinder <-> Dell Storage Center integration as part
of an OpenStack deployment.
"""

import logging
import typing

import ops
import pydantic
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeDellSCOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Dell SC Operator charm."""

    service_name = "cinder-volume-dellsc"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "dellsc." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()
        # DellSC has no use for driver-ssl-cert in charm config. The driver will always use the system CA bundle to validate API connectivity if dell-sc-verify-cert is true
        overrides.pop("driver-ssl-cert", None)
        overrides.update(
            {
                "san-ip": typing.Annotated[
                    pydantic.IPvAnyAddress, sunbeam_storage.Required
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
                "dell-sc-ssn": typing.Annotated[int, sunbeam_storage.Required],
                "protocol": typing.Annotated[
                    typing.Literal["fc", "iscsi"], sunbeam_storage.Required
                ],
                "secondary-san-ip": typing.Annotated[
                    str, sunbeam_storage.RequiredIfGroup("secondary")
                ],
                "secondary-san-login": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("secondary-san-login")
                    ),
                    sunbeam_storage.RequiredIfGroup("secondary"),
                ],
                "secondary-san-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("secondary-san-password")
                    ),
                    sunbeam_storage.RequiredIfGroup("secondary"),
                ],
                "enable-unsupported-driver": typing.Literal[True],
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeDellSCOperatorCharm)
