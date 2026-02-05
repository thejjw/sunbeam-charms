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

    def context(self) -> dict:
        """Generate context information for cinder config."""
        config = self.charm.model.config
        backend_name = config.get("volume-backend-name") or self.charm.app.name
        san_ip = config.get("san-ip")
        if not san_ip:
            raise sunbeam_guard.WaitingExceptionError("Missing required san-ip")
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

        primary_secret = self._get_optional_secret("san-credentials-secret")
        dellsc_secret = self._get_optional_secret("dellsc-config-secret")

        if dellsc_secret is None and primary_secret is None:
            raise sunbeam_guard.WaitingExceptionError(
                "Missing required credentials secret"
            )

        if dellsc_secret is not None and primary_secret is not None:
            raise sunbeam_guard.WaitingExceptionError(
                "Only one of dellsc-config-secret or san-credentials-secret may be set"
            )

        config_dict.pop("dellsc-config-secret", None)
        config_dict.pop("san-credentials-secret", None)

        if dellsc_secret is not None:
            config_dict["san-login"] = self._get_secret_field(
                dellsc_secret, "primary-username"
            )
            config_dict["san-password"] = self._get_secret_field(
                dellsc_secret, "primary-password"
            )
        else:
            config_dict["san-login"] = self._get_secret_field(
                primary_secret, "username"  # type: ignore[arg-type]
            )
            config_dict["san-password"] = self._get_secret_field(
                primary_secret, "password"  # type: ignore[arg-type]
            )

        secondary_ip = config.get("secondary-san-ip")
        secondary_secret = self._get_optional_secret(
            "secondary-san-credentials-secret"
        )

        if dellsc_secret is not None and secondary_ip:
            config_dict["secondary-san-login"] = self._get_secret_field(
                dellsc_secret, "secondary-username"
            )
            config_dict["secondary-san-password"] = self._get_secret_field(
                dellsc_secret, "secondary-password"
            )
        elif secondary_secret is not None:
            config_dict.pop("secondary-san-credentials-secret", None)
            config_dict["secondary-san-login"] = self._get_secret_field(
                secondary_secret, "username"
            )
            config_dict["secondary-san-password"] = self._get_secret_field(
                secondary_secret, "password"
            )

        if secondary_ip and "secondary-san-login" not in config_dict:
            raise sunbeam_guard.WaitingExceptionError(
                "Secondary credentials are required when secondary-san-ip is set"
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
                "san-credentials-secret": ops.Secret | None,
                "secondary-san-credentials-secret": ops.Secret | None,
                "dellsc-config-secret": ops.Secret | None,
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
