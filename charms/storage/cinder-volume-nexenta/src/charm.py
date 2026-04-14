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

"""Cinder nexenta Operator Charm.

This charm provide Cinder <-> nexenta integration as part
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


class RestProtocol(StrEnum):
    """Enumeration of valid REST protocol types."""

    HTTP = "http"
    HTTPS = "https"
    AUTO = "auto"


class DatasetCompression(StrEnum):
    """Enumeration of valid dataset compression types."""

    ON = "on"
    OFF = "off"
    GZIP = "gzip"
    GZIP_1 = "gzip-1"
    GZIP_2 = "gzip-2"
    GZIP_3 = "gzip-3"
    GZIP_4 = "gzip-4"
    GZIP_5 = "gzip-5"
    GZIP_6 = "gzip-6"
    GZIP_7 = "gzip-7"
    GZIP_8 = "gzip-8"
    GZIP_9 = "gzip-9"
    LZJB = "lzjb"
    ZLE = "zle"
    LZ4 = "lz4"


class DatasetDedup(StrEnum):
    """Enumeration of valid dataset deduplication types."""

    ON = "on"
    OFF = "off"
    SHA256 = "sha256"
    VERIFY = "verify"
    SHA256_VERIFY = "sha256, verify"


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeNexentaOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Nexenta Operator charm."""

    service_name = "cinder-volume-nexenta"

    @property
    def backend_key(self) -> str:
        """Return the Cinder backend section key for Nexenta."""
        return "nexenta." + self.model.app.name

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        overrides = super()._configuration_type_overrides()
        overrides.pop("driver-ssl-cert", None)
        overrides.update(
            {
                "nexenta-rest-password": typing.Annotated[
                    str,
                    pydantic.BeforeValidator(sunbeam_storage.secret_validator("nexenta-rest-password")),
                    sunbeam_storage.Required,
                ],
                "nexenta-rest-protocol": RestProtocol | None,
                "nexenta-dataset-compression": DatasetCompression | None,
                "nexenta-dataset-dedup": DatasetDedup | None,
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeNexentaOperatorCharm)
