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

"""Cinder Infinidat Operator Charm."""

import functools
import logging
import typing

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeInfinidatOperatorCharm(
    charm.OSCinderVolumeDriverOperatorCharm
):
    """Cinder/Infinidat Operator charm."""

    service_name = "cinder-volume-infinidat"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "infinidat." + self.model.app.name

    @functools.cached_property
    def configuration_class(self) -> type[pydantic.BaseModel]:
        """Configuration class with Infinidat-specific validation."""
        base_class = super().configuration_class

        class InfinidatConfigModel(base_class):
            """Infinidat configuration model."""

            @pydantic.model_validator(mode="after")
            def validate_iscsi_netspaces(self) -> "InfinidatConfigModel":
                """Require iSCSI netspaces when the iSCSI protocol is selected."""
                if (
                    self.protocol == "iscsi"
                    and not self.infinidat_iscsi_netspaces
                ):
                    raise ValueError(
                        "infinidat-iscsi-netspaces is required "
                        "when protocol is 'iscsi'"
                    )
                return self

        return InfinidatConfigModel

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()
        overrides.update(
            {
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
                "infinidat-pool-name": typing.Annotated[
                    str, sunbeam_storage.Required
                ],
                "protocol": typing.Literal["fc", "iscsi"],
                "chap-username": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("chap-username")
                    ),
                    sunbeam_storage.RequiredIfGroup("chap-authentication"),
                ],
                "chap-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("chap-password")
                    ),
                    sunbeam_storage.RequiredIfGroup("chap-authentication"),
                ],
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeInfinidatOperatorCharm)
