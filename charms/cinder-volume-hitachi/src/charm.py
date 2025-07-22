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
from __future__ import (
    annotations,
)

import logging
import shutil
import subprocess
from functools import (
    lru_cache,
)
from pathlib import (
    Path,
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


def prepare_iscsi_initiator_file():
    """Copy host initiatorname.iscsi into snap layout path if needed."""
    source = Path("/etc/iscsi/initiatorname.iscsi")
    target = Path(
        "/var/snap/cinder-volume/common/etc/iscsi/initiatorname.iscsi"
    )

    if not source.exists():
        logger.warning(
            "Host iSCSI initiator file not found at /etc/iscsi/initiatorname.iscsi"
        )
        return

    try:
        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        # Only copy if content differs
        if not target.exists() or source.read_text() != target.read_text():
            shutil.copy2(source, target)
            logger.info(f"Copied {source} to {target}")
        else:
            logger.debug(
                "iSCSI initiator file already present and up to date."
            )

    except Exception as e:
        logger.error(f"Failed to prepare iSCSI initiator file: {e}")


def connect_snap_interfaces() -> None:
    """Attempt to connect all interfaces the snap needs.

    Missing or already–connected interfaces are logged but do not
    abort the charm start-up.
    """
    interfaces = [
        "mount-observe",
        "system-observe",
        "hardware-observe",
        "log-observe",
        "network-observe",
        "block-devices",
        "etc-iscsi",
        "nvme-identity",  # ← NEW (system-files plug for /etc/nvme/host{nqn,id})
    ]

    for iface in interfaces:
        plug = f"cinder-volume:{iface}"
        slot = f":{iface}"
        try:
            subprocess.run(["snap", "connect", plug, slot], check=True)
            logger.info("Connected interface %s", iface)
        except subprocess.CalledProcessError as exc:
            # Return-code 1 is “already connected” for `snap connect`
            if exc.returncode == 1:
                logger.debug("Interface %s already connected", iface)
            else:
                logger.warning(
                    "Failed to connect interface %s: %s", iface, exc
                )


@sunbeam_tracing.trace_type
class HitachiConfigurationContext(config_contexts.ConfigContext):
    """Expose *all* charm config options as Jinja context for the backend."""

    charm: "CinderVolumeHitachiOperatorCharm"

    def context(self) -> dict:
        """Builds and returns the configuration context."""
        cfg = self.charm.model.config

        # Mandatory basics --------------------------------------------------
        backend_name = cfg.get("volume-backend-name") or self.charm.app.name
        context: dict[str, str | int | bool | None] = {
            "backend_name": backend_name,
            "backend_availability_zone": cfg.get("backend-availability-zone"),
        }

        # copy every non‑empty charm option into its driver key
        for key, value in cfg.items():
            if value in (None, ""):
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

    # Charm hooks
    def configure_charm(self, event: ops.EventBase):  # noqa: D401
        """Run each time configuration or relations change."""
        super().configure_charm(event)
        connect_snap_interfaces()
        prepare_iscsi_initiator_file()
        # No extra services or relations for Hitachi.

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
            "san-login",
            "san-password",
            "hitachi-storage-id",
            "hitachi-pools",
        }
        missing = [opt for opt in mandatory if not cfg.get(opt)]
        if missing:
            raise sunbeam_guard.WaitingExceptionError(
                f"Mandatory options missing: {', '.join(missing)}"
            )

        # ---------- build minimal-delta stanza ----------
        stanza: dict[str, str | int | bool | None] = {
            "volume-backend-name": cfg.get("volume-backend-name")
            or self.app.name,
        }

        for key, value in cfg.items():
            # skip the wrapper keys already handled
            if key in ("volume-backend-name"):
                continue
            # drop empty strings and values still equal to the declared default
            if value == "" or value == defaults.get(key):
                continue
            stanza[key] = value

        return stanza

    #  Config contexts
    @property
    def config_contexts(self):  # noqa: D401, ANN001
        """Append our Hitachi context to the default list."""
        return super().config_contexts + [
            HitachiConfigurationContext(self, "hitachi"),
        ]


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeHitachiOperatorCharm)
