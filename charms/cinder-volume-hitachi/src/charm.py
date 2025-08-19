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
from functools import (
    lru_cache,
)
from typing import (
    Mapping,
)

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


# Helpers
@lru_cache(maxsize=None)
def _config_defaults(charm) -> dict[str, object]:
    """Return {option_name: default_value} for this charm instance."""
    return {k: opt.default for k, opt in charm.meta.config.items()}


@sunbeam_tracing.trace_type
class HitachiConfigurationContext(config_contexts.ConfigContext):
    """Expose *all* charm config options as Jinja context for the backend."""

    charm: "CinderVolumeHitachiOperatorCharm"

    def context(self) -> dict:
        """Builds and returns the configuration context."""
        cfg = self.charm.model.config
        defaults = _config_defaults(self.charm)

        # Mandatory basics --------------------------------------------------
        backend_name = cfg.get("volume-backend-name") or self.charm.app.name
        context: dict[str, str | int | bool | None] = {
            "backend_name": backend_name,
            "backend_availability_zone": cfg.get("backend-availability-zone"),
        }

        # copy every non‑empty or default charm option into its driver key
        for key, value in cfg.items():
            if value in (None, "") or value == defaults.get(key):
                continue  # skip unset / empty values
            # Preserve the two already handled above
            if key in ("volume-backend-name", "backend-availability-zone"):
                continue
            context[key] = value

        return context


# Operator charm
@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeHitachiOperatorCharm(
    charm.OSCinderVolumeDriverOperatorCharm,
):
    """Operator charm for the Hitachi VSP Cinder driver."""

    # systemd service label (inside the workload container)
    service_name = "cinder-volume-hitachi"

    # Keys & identifiers
    @property
    def backend_key(self) -> str:  # noqa: D401
        """Key under which the backend config is published to the snap."""
        return "hitachi." + self.model.app.name

    # Secret handling helpers
    def _get_credentials_from_secret(
        self, secret_uri: str, secret_type: str
    ) -> tuple[str, str]:
        """Retrieve username and password from a Juju secret.

        Args:
            secret_uri: The secret URI to retrieve
            secret_type: Description of the secret type for error messages

        Returns:
            Tuple of (username, password)

        Raises:
            WaitingExceptionError: If secret retrieval fails
        """
        try:
            secret = self.model.get_secret(id=secret_uri)
            secret_content = secret.get_content()
            return secret_content["username"], secret_content["password"]
        except Exception as e:
            raise sunbeam_guard.WaitingExceptionError(
                f"Failed to retrieve {secret_type} credentials from secret {secret_uri}: {e}"
            )

    def _add_secret_credentials_to_stanza(
        self, cfg: dict, stanza: dict
    ) -> None:
        """Add all secret-based credentials to the configuration stanza.

        Args:
            cfg: Charm configuration dictionary
            stanza: Configuration stanza to update
        """
        # Handle array credentials secret (required)
        if san_secret_uri := cfg.get("san-credentials-secret"):
            username, password = self._get_credentials_from_secret(
                san_secret_uri, "array"
            )
            stanza["san-login"] = username
            stanza["san-password"] = password

        # Handle CHAP credentials secret (optional)
        if chap_secret_uri := cfg.get("chap-credentials-secret"):
            username, password = self._get_credentials_from_secret(
                chap_secret_uri, "CHAP"
            )
            stanza["chap-username"] = username
            stanza["chap-password"] = password

        # Handle mirror CHAP credentials secret (optional)
        if mirror_chap_secret_uri := cfg.get(
            "hitachi-mirror-chap-credentials-secret"
        ):
            username, password = self._get_credentials_from_secret(
                mirror_chap_secret_uri, "mirror CHAP"
            )
            stanza["hitachi-mirror-auth-user"] = username
            stanza["hitachi-mirror-auth-password"] = password

        # Handle mirror REST credentials secret (optional)
        if mirror_rest_secret_uri := cfg.get(
            "hitachi-mirror-rest-credentials-secret"
        ):
            username, password = self._get_credentials_from_secret(
                mirror_rest_secret_uri, "mirror REST"
            )
            stanza["hitachi-mirror-rest-user"] = username
            stanza["hitachi-mirror-rest-password"] = password

    # Backend configuration
    def get_backend_configuration(self) -> Mapping:
        """Return a backend dict ready for snap.set().

        • Supports **every** option declared under `config:` in *charmcraft.yaml*
        • Omits values that are still equal to their default
        """
        cfg = self.model.config
        defaults = _config_defaults(self)

        # ---------- verify mandatory input ----------
        mandatory = {
            "san-ip",
            "san-credentials-secret",
            "hitachi-storage-id",
            "hitachi-pools",
        }
        missing = [opt for opt in mandatory if not cfg.get(opt)]
        if missing:
            raise sunbeam_guard.WaitingExceptionError(
                f"Mandatory options missing: {', '.join(missing)}"
            )

        # ---------- handle secrets and build stanza ----------
        stanza: dict[str, str | int | bool | None] = {
            "volume-backend-name": cfg.get("volume-backend-name")
            or self.app.name,
        }

        # Add all secret-based credentials
        self._add_secret_credentials_to_stanza(cfg, stanza)

        # Add all other non-secret configuration options
        for key, value in cfg.items():
            # Skip secret URIs and already handled keys
            if key in (
                "volume-backend-name",
                "san-credentials-secret",
                "chap-credentials-secret",
                "hitachi-mirror-chap-credentials-secret",
                "hitachi-mirror-rest-credentials-secret",
            ):
                continue
            # Drop empty strings and values still equal to the declared default
            if value == "" or value == defaults.get(key):
                continue
            stanza[key] = value

        return stanza

    #  Config contexts
    @property
    def config_contexts(self):
        """Append our Hitachi context to the default list."""
        return super().config_contexts + [
            HitachiConfigurationContext(self, "hitachi"),
        ]


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeHitachiOperatorCharm)
