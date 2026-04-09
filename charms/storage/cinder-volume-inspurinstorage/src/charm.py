#!/usr/bin/env python3

#
# Copyright 2026 Canonical Ltd.
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

"""Cinder inspurinstorage Operator Charm.

This charm provide Cinder <-> inspurinstorage integration as part
of an OpenStack deployment
"""

import logging
from enum import StrEnum
import typing

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic

logger = logging.getLogger(__name__)


class Protocol(StrEnum):
    """Enumeration of valid protocol types."""

    FC = "fc"
    ISCSI = "iscsi"


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeInspurinstorageOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Inspurinstorage Operator charm."""

    service_name = "cinder-volume-inspurinstorage"

    @property
    def backend_key(self) -> str:
        """Return the Cinder backend section key for Inspur InStorage."""
        return "inspurinstorage." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        overrides = super()._configuration_type_overrides()
        overrides.pop("driver-ssl-cert", None)
        overrides.update(
            {
                "protocol": typing.Annotated[
                    typing.Literal["fc", "iscsi"], sunbeam_storage.Required
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
    ops.main(CinderVolumeInspurinstorageOperatorCharm)
