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

import ipaddress
import logging
import typing

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.storage as sunbeam_storage
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class DellSCConfigurationContext(config_contexts.ConfigContext):
    """Configuration context that expands Dell SC credential secrets."""

    charm: "CinderVolumeDellSCOperatorCharm"
    _SECRET_KEY_MAP = {
        "san-login": ("san-login",),
        "san-password": ("san-password",),
        "secondary-san-login": ("secondary-san-login",),
        "secondary-san-password": ("secondary-san-password",),
    }

    def _get_secret_field(self, secret: ops.Secret, field: str) -> str:
        """Return a single required field from a Juju secret."""
        return sunbeam_storage.secret_validator(field)(secret)

    def _resolve_secret(self, value: ops.Secret | str | None) -> ops.Secret | None:
        """Resolve a secret from config value or secret URI."""
        if value is None:
            return None
        if isinstance(value, ops.Secret):
            return value
        if isinstance(value, str):
            return self.charm.model.get_secret(id=value)
        raise sunbeam_guard.WaitingExceptionError(
            "Invalid secret value type for DellSC credentials"
        )

    def _get_optional_secret(self, key: str) -> ops.Secret | None:
        """Return an optional secret value."""
        return self._resolve_secret(self.charm.model.config.get(key))

    def _get_secret_value(self, key: str, required: bool = False) -> str | None:
        """Resolve a secret value for a config key.

        Accepts either a dedicated secret with a single key/value or a shared
        secret containing multiple Dell SC credential fields.
        """
        secret = self._get_optional_secret(key)
        if secret is None:
            if required:
                raise sunbeam_guard.WaitingExceptionError(
                    f"Missing required {key} secret"
                )
            return None

        for field in self._SECRET_KEY_MAP[key]:
            try:
                return self._get_secret_field(secret, field)
            except ValueError:
                continue

        valid_keys = ", ".join(self._SECRET_KEY_MAP[key])
        raise sunbeam_guard.WaitingExceptionError(
            f"Secret for {key} must contain one of: {valid_keys}"
        )

    def context(self) -> dict:
        """Generate context information for cinder config."""
        config = self.charm.model.config
        backend_name = config.get("volume-backend-name") or self.charm.app.name
        san_ip = config.get("san-ip")
        dell_sc_ssn = config.get("dell-sc-ssn")
        if not san_ip:
            raise sunbeam_guard.WaitingExceptionError("Missing required san-ip")
        if dell_sc_ssn is None:
            raise sunbeam_guard.WaitingExceptionError("Missing required dell-sc-ssn")
        # snap-cinder-volume requires an IP address for san_ip, not a hostname.
        try:
            ipaddress.ip_address(san_ip)
        except ValueError as exc:
            raise sunbeam_guard.WaitingExceptionError(
                "san-ip must be an IP address to satisfy snap-cinder-volume"
            ) from exc

        config_dict = {k: v for k, v in config.items() if v is not None}
        config_dict["volume-backend-name"] = backend_name
        config_dict["enable_unsupported_driver"] = True

        for key in (
            "san-login",
            "san-password",
            "secondary-san-login",
            "secondary-san-password",
        ):
            config_dict.pop(key, None)

        config_dict["san-login"] = self._get_secret_value("san-login", required=True)
        config_dict["san-password"] = self._get_secret_value(
            "san-password", required=True
        )

        secondary_ip = config.get("secondary-san-ip")
        if secondary_ip:
            config_dict["secondary-san-login"] = self._get_secret_value(
                "secondary-san-login", required=True
            )
            config_dict["secondary-san-password"] = self._get_secret_value(
                "secondary-san-password", required=True
            )

        return config_dict


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeDellSCOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Dell SC Operator charm."""

    service_name = "cinder-volume-dellsc"

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "dellsc." + self.model.app.name

    @property
    def config_contexts(self) -> list[config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        return [DellSCConfigurationContext(self, "backend")]

    def _configuration_type_overrides(self) -> dict[str, typing.Any]:
        """Configuration type overrides for pydantic model generation."""
        overrides = super()._configuration_type_overrides()
        overrides.update(
            {
                "san-login": ops.Secret | None,
                "san-password": ops.Secret | None,
                "secondary-san-login": ops.Secret | None,
                "secondary-san-password": ops.Secret | None,
                "protocol": typing.Literal["fc", "iscsi"] | None,
                "dell-sc-ssn": int | None,
                "dell-sc-api-port": int | None,
                "secondary-sc-api-port": int | None,
                "dell-api-async-rest-timeout": int | None,
                "dell-api-sync-rest-timeout": int | None,
                "ssh-conn-timeout": int | None,
                "ssh-max-pool-conn": int | None,
                "ssh-min-pool-conn": int | None,
            }
        )
        return overrides


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeDellSCOperatorCharm)
