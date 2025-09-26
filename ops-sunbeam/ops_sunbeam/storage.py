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

"""Storage backend configuration and validation for Cinder volume drivers.

This module provides Pydantic models and configuration contexts for
OpenStack Cinder volume driver charms. It includes base classes for
generic Cinder volume configuration as well as utilities for validating
storage backend certificates and other security-related configurations.
"""

import datetime
import logging
from typing import (
    TYPE_CHECKING,
    Annotated,
)

import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic
import pydantic.alias_generators
from cryptography import (
    x509,
)
from pydantic import (
    BaseModel,
    Field,
)

if TYPE_CHECKING:
    import ops_sunbeam.charm

logger = logging.getLogger(__name__)


def to_kebab(value: str) -> str:
    """Convert a string to kebab-case."""
    return pydantic.alias_generators.to_snake(value).replace("_", "-")


def certificate_validator(value: str | None) -> str | None:
    """Validate that the certificate is PEM formatted."""
    if value is None:
        return value
    if not isinstance(value, str):
        raise ValueError("Certificate must be a string")

    certificate = value
    if "-----BEGIN CERTIFICATE-----" not in certificate:
        raise ValueError("Certificate must be PEM formatted")

    try:
        cert = x509.load_pem_x509_certificate(certificate.encode())
        if cert.not_valid_after < datetime.datetime.now():
            raise ValueError("Certificate has expired")
    except Exception as e:
        logger.error(f"Failed to validate certificate: {e}")
        raise ValueError("Invalid certificate format")

    return certificate


class CinderVolumeConfig(BaseModel):
    """Pydantic model for generic Cinder volume configuration options."""

    model_config = pydantic.ConfigDict(
        alias_generator=pydantic.AliasGenerator(
            serialization_alias=to_kebab,
        ),
        arbitrary_types_allowed=True,
    )

    volume_backend_name: str = Field(
        description="Name that Cinder will report for this backend. If unset the Juju application name is used.",
    )
    backend_availability_zone: str | None = Field(
        default=None,
        description="Availability zone to associate with this backend.",
    )
    driver_ssl_cert: Annotated[
        str | None, pydantic.BeforeValidator(certificate_validator)
    ] = Field(
        default=None,
        description="SSL certificate to trust remote storage backend.",
    )
    protocol: str = Field(
        default="iscsi",
        description="Storage protocol selector, determines driver class to use: iscsi, fc, or nvme",
    )
    san_ip: str = Field(description="Storage array management IP address or hostname.")
    use_multipath_for_image_xfer: bool = Field(
        default=True,
        description="Enable multipathing for image transfer operations to improve performance and reliability.",
    )


@sunbeam_tracing.trace_type
class CinderVolumeConfigurationContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

    charm: "ops_sunbeam.charm.OSCinderVolumeDriverOperatorCharm"

    def context(self) -> dict:
        """Generate context information for cinder config."""
        backend_name = (
            self.charm.model.config.get("volume-backend-name") or self.charm.app.name
        )
        config = self.charm.load_config(
            self.charm.configuration_class,
            volume_backend_name=backend_name,
        )
        return config.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
