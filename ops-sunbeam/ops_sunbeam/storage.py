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

import dataclasses
import datetime
import logging
import typing
from functools import (
    cache,
)
from typing import (
    TYPE_CHECKING,
    Annotated,
)

import ops
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.tracing as sunbeam_tracing
import pydantic
import pydantic.alias_generators
import pydantic_core
from cryptography import (
    x509,
)
from pydantic import (
    BaseModel,
)

if TYPE_CHECKING:
    import ops_sunbeam.charm

logger = logging.getLogger(__name__)


Required = pydantic.Field(...)


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


@cache
def _secret_fetcher(secret: ops.Secret) -> dict[str, str]:
    """Helper to fetch secret content.

    This is cached once per hook instance to avoid re-fetching the same secret
    multiple times during  the same hook execution.
    """
    return secret.get_content(refresh=True)


def secret_validator(key: str) -> typing.Callable[[ops.Secret], str]:
    """Validate that the secret is not empty for the specific key."""

    def _validator(value: ops.Secret) -> str:
        secret = _secret_fetcher(value)
        secret_value = secret.get(key)
        if secret_value is None or not secret_value.strip():
            raise ValueError(f"Secret must contain non-empty '{key}' field")
        return secret_value

    return _validator


@dataclasses.dataclass(frozen=True)
class RequiredIfGroup:
    """Annotation to mark a field as part of a required group.

    If any field in the group is set, all fields in the group must be set.
    """

    group: str


def _to_pydantic_field(
    name: str,
    meta: ops.ConfigMeta,
    override: dict[str, typing.Any],
) -> tuple[typing.Any, pydantic.fields.FieldInfo]:
    """Generate a Pydantic field type and FieldInfo from config metadata.

    This is a helper function for `to_pydantic_class`.
    """
    if name in override:
        field_type = override[name]
    else:
        if meta.type == "boolean":
            field_type = bool
        elif meta.type == "int":
            field_type = int
        elif meta.type == "float":
            field_type = float
        else:
            field_type = str

        if meta.default is None:
            field_type = field_type | None

    default_value = meta.default
    description = meta.description

    if typing.get_origin(field_type) is Annotated:
        for arg in typing.get_args(field_type):
            if isinstance(arg, pydantic.fields.FieldInfo):
                default_value = arg.default
                if arg.description is not None:
                    description = arg.description

    return (
        field_type,
        pydantic.Field(default=default_value, description=description),  # type: ignore
    )


def create_group_validator(
    groups: dict[str, list[str]],
) -> typing.Callable[[typing.Any], typing.Any]:
    """Create a validator for required groups."""

    def group_validator(self: typing.Any) -> typing.Any:
        validation_errors = []
        for group_name, field_names in groups.items():
            set_fields = [
                f for f in field_names if getattr(self, f) is not None
            ]
            if set_fields:
                missing_fields = [
                    f for f in field_names if getattr(self, f) is None
                ]
                if missing_fields:
                    validation_errors.append(
                        pydantic_core.InitErrorDetails(
                            type=pydantic_core.PydanticCustomError(
                                "RequiredIfGroupError",
                                "Fields {missing_fields} must be set when {set_fields} are set (Group: {group_name})",
                                {
                                    "missing_fields": str(missing_fields),
                                    "set_fields": str(set_fields),
                                    "group_name": group_name,
                                },
                            ),
                            loc=tuple(missing_fields),
                            input=group_name,
                            ctx={},
                        )
                    )
        if validation_errors:
            raise pydantic_core.ValidationError.from_exception_data(
                title="ConfigModel",
                line_errors=validation_errors,
            )
        return self

    return group_validator


def to_pydantic_class(
    config_definition: dict[str, ops.ConfigMeta],
    override: dict[str, typing.Any],
) -> type[pydantic.BaseModel]:
    """Generate a Pydantic model class from config metadata.

    Given a dictionary of config metadata, generate a Pydantic model class
    with fields corresponding to the config options. The `override` parameter
    allows specifying custom types or validators for specific fields.
    """
    unknown_overrides = set(override.keys()) - set(config_definition.keys())
    if unknown_overrides:
        raise ValueError(
            f"Overrides defined for unknown config options: {unknown_overrides}"
        )

    fields = {}
    groups: dict[str, list[str]] = {}

    for name, meta in config_definition.items():
        snake_name = pydantic.alias_generators.to_snake(name)
        field_type, field_info = _to_pydantic_field(name, meta, override)

        if typing.get_origin(field_type) is Annotated:
            for arg in typing.get_args(field_type):
                if isinstance(arg, RequiredIfGroup):
                    groups.setdefault(arg.group, []).append(snake_name)

        fields[snake_name] = (field_type, field_info)

    validators = {}
    if groups:
        validators["validate_groups"] = pydantic.model_validator(mode="after")(
            create_group_validator(groups)
        )

    return pydantic.create_model(  # type: ignore
        "ConfigModel",
        __config__=pydantic.ConfigDict(
            alias_generator=pydantic.AliasGenerator(
                serialization_alias=to_kebab,
            ),
            arbitrary_types_allowed=True,
        ),
        __validators__=validators,
        **fields,  # type: ignore
    )


@sunbeam_tracing.trace_type
class CinderVolumeConfigurationContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

    charm: "ops_sunbeam.charm.OSCinderVolumeDriverOperatorCharm"

    def context(self) -> dict:
        """Generate context information for cinder config."""
        backend_name = (
            self.charm.model.config.get("volume-backend-name")
            or self.charm.app.name
        )
        config: BaseModel = self.charm.load_config(
            self.charm.configuration_class,
            volume_backend_name=backend_name,
        )
        return config.model_dump(
            by_alias=True,
            exclude_none=True,
            mode="json",
        )
