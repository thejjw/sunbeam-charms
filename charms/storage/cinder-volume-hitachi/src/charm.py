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

"""Cinder ↔︎ Hitachi VSP operator charm for Sunbeam.

This charm wires the *cinder-volume* snap to a Hitachi VSP storage
backend.  It contributes one backend stanza (``hitachi.<app-name>.*``)
with *all* officially supported driver options.  Only the standard
``cinder-volume`` relation is required – no Ceph or secret distribution
is involved.
"""

import logging
import typing

import ops
import pydantic
import ops_sunbeam.charm as charm
import ops_sunbeam.tracing as sunbeam_tracing

import ops_sunbeam.storage as sunbeam_storage

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeHitachiOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Hitachi Operator charm."""

    service_name = "cinder-volume-hitachi"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "hitachi." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()
        overrides.update(
            {
                "san-username": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("san-username")
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
                "hitachi-storage-id": typing.Annotated[str, sunbeam_storage.Required],
                "hitachi-pools": typing.Annotated[str, sunbeam_storage.Required],
                "protocol": typing.Annotated[
                    typing.Literal["fc", "iscsi"], sunbeam_storage.Required
                ],
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
                "hitachi-mirror-ssl-cert": typing.Annotated[
                    str | None,
                    pydantic.BeforeValidator(sunbeam_storage.certificate_validator),
                ],
                "hitachi-mirror-auth-username": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("hitachi-mirror-auth-username")
                    ),
                    sunbeam_storage.RequiredIfGroup(
                        "hitachi-mirror-chap-authentication"
                    ),
                ],
                "hitachi-mirror-auth-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("hitachi-mirror-auth-password")
                    ),
                    sunbeam_storage.RequiredIfGroup(
                        "hitachi-mirror-chap-authentication"
                    ),
                ],
                "hitachi-mirror-rest-username": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("hitachi-mirror-rest-username")
                    ),
                    sunbeam_storage.RequiredIfGroup(
                        "hitachi-mirror-rest-authentication"
                    ),
                    sunbeam_storage.RequiredIfGroup("hitachi-mirror"),
                ],
                "hitachi-mirror-rest-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(
                        sunbeam_storage.secret_validator("hitachi-mirror-rest-password")
                    ),
                    sunbeam_storage.RequiredIfGroup(
                        "hitachi-mirror-rest-authentication"
                    ),
                    sunbeam_storage.RequiredIfGroup("hitachi-mirror"),
                ],
                "hitachi-mirror-rest-api-ip": typing.Annotated[
                    str,
                    sunbeam_storage.RequiredIfGroup("hitachi-mirror"),
                ],
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeHitachiOperatorCharm)
