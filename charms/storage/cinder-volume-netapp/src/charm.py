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

"""Cinder NetApp Operator Charm.

This charm provide Cinder <-> NetApp integration as part
of an OpenStack deployment
"""

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


class Family(StrEnum):
    """Enumeration of valid storage family types."""
    ONTAP_CLUSTER = "ontap_cluster"


class TransportType(StrEnum):
    """Enumeration of valid transport types."""
    HTTP = "http"
    HTTPS = "https"


class LunSpaceReservation(StrEnum):
    """Enumeration of valid LUN space reservation options."""
    ENABLED = "enabled"
    DISABLED = "disabled"


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeNetAppOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/NetApp Operator charm."""

    service_name = "cinder-volume-netapp"

    @property
    def backend_key(self) -> str:
        """Return the Cinder backend section key for NetApp."""
        return "netapp." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        overrides = super()._configuration_type_overrides()
        overrides.pop("driver-ssl-cert", None)
        overrides.update(
            {
                "protocol": typing.Annotated[
                    typing.Literal["iscsi", "nvme"], sunbeam_storage.Required
                ],
                "netapp-storage-family": Family | None,
                "netapp-storage-protocol": typing.Annotated[
                    typing.Literal["iscsi", "fc", "nfs", "nvme"], sunbeam_storage.Required
                ],
                "netapp-transport-type": TransportType | None,
                "netapp-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("netapp-password")),
                ],
                "netapp-private-key-file": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("netapp-private-key-file")),
                ],
                "netapp-certificate-file": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("netapp-certificate-file")),
                ],
                "netapp-ca-certificate-file": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("netapp-ca-certificate-file")),
                    sunbeam_storage.Required,
                ],
                "netapp-lun-space-reservation": LunSpaceReservation | None,
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeNetAppOperatorCharm)
