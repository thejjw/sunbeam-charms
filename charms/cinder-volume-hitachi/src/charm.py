#!/usr/bin/env python3
"""Cinder ↔︎ Hitachi VSP operator charm for Sunbeam.

This charm wires the *cinder-volume* snap to a Hitachi VSP storage
backend.  It contributes one backend stanza (``hitachi.<app-name>.*``)
with *all* officially supported driver options.  Only the standard
``cinder-volume`` relation is required – no Ceph or secret distribution
is involved.
"""
from __future__ import annotations

import logging
from typing import Mapping

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing
import subprocess
from pathlib import Path
import shutil
import logging


logger = logging.getLogger(__name__)


def prepare_iscsi_initiator_file():
    """Copy host initiatorname.iscsi into snap layout path if needed."""
    source = Path("/etc/iscsi/initiatorname.iscsi")
    target = Path("/var/snap/cinder-volume/common/etc/iscsi/initiatorname.iscsi")

    if not source.exists():
        logger.warning("Host iSCSI initiator file not found at /etc/iscsi/initiatorname.iscsi")
        return

    try:
        # Ensure parent directory exists
        target.parent.mkdir(parents=True, exist_ok=True)

        # Only copy if content differs
        if not target.exists() or source.read_text() != target.read_text():
            shutil.copy2(source, target)
            logger.info(f"Copied {source} to {target}")
        else:
            logger.debug("iSCSI initiator file already present and up to date.")

    except Exception as e:
        logger.error(f"Failed to prepare iSCSI initiator file: {e}")

import subprocess
import logging

logger = logging.getLogger(__name__)

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
        "nvme-identity",   # ← NEW (system-files plug for /etc/nvme/host{nqn,id})
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
                logger.warning("Failed to connect interface %s: %s", iface, exc)


@sunbeam_tracing.trace_type
class HitachiConfigurationContext(config_contexts.ConfigContext):
    """Expose *all* charm config options as Jinja context for the backend."""

    charm: "CinderVolumeHitachiOperatorCharm"

    def context(self) -> dict:  # noqa: D401, ANN001
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


# ---------------------------------------------------------------------------
#  Operator charm
# ---------------------------------------------------------------------------

@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeHitachiOperatorCharm(
    charm.OSCinderVolumeDriverOperatorCharm,
):
    """Operator charm for the Hitachi VSP Cinder driver."""

    # systemd service label (inside the workload container)
    service_name = "cinder-volume-hitachi"

    # ------------------------------------------------------------------
    #  Keys & identifiers
    # ------------------------------------------------------------------
    @property
    def backend_key(self) -> str:  # noqa: D401
        """Key under which the backend config is published to the snap."""
        return "hitachi." + self.model.app.name

    # ------------------------------------------------------------------
    #  Charm hooks
    # ------------------------------------------------------------------
    def configure_charm(self, event: ops.EventBase):  # noqa: D401
        """Run each time configuration or relations change."""
        super().configure_charm(event)
        connect_snap_interfaces()
        prepare_iscsi_initiator_file()
        # No extra services or relations for Hitachi.
    

    # ------------------------------------------------------------------
    #  Backend configuration
    # ------------------------------------------------------------------
    def get_backend_configuration(self) -> Mapping[str, typing.Any]:
        """Return a backend dict ready for snap.set()."""
        ctxs = self.contexts()
        try:
            hitachi_ctx = ctxs.hitachi          # type: ignore[attr-defined]
        except AttributeError as exc:
            raise sunbeam_guard.WaitingExceptionError(
                f"Data missing: {exc}"
            ) from exc

        # ---------------- Mandatory check ----------------
        mandatory = [
            "san-ip",
            "san-login",
            "san-password",
            "hitachi-storage-id",
            "hitachi-pools",
        ]
        missing = [m for m in mandatory if not hitachi_ctx.context().get(m)]
        if missing:
            raise sunbeam_guard.WaitingExceptionError(
                f"Mandatory options missing: {', '.join(missing)}"
            )

        # -------------- Build clean dict -----------------
        backend_cfg: dict[str, typing.Any] = {
            k: v
            for k, v in hitachi_ctx.context().items()   # <- SOLO los datos
            if v not in (None, "")                      # omite vacíos
        }

        # Extra claves que no vienen del usuario
        backend_cfg["volume-backend-name"] = (
            backend_cfg.get("volume-backend-name") or self.app.name
        )
        az = backend_cfg.pop("backend-availability-zone", None)
        if az:
            backend_cfg["backend-availability-zone"] = az

        return backend_cfg

    # ------------------------------------------------------------------
    #  Config contexts
    # ------------------------------------------------------------------
    @property
    def config_contexts(self):  # noqa: D401, ANN001
        """Append our Hitachi context to the default list."""
        return super().config_contexts + [
            HitachiConfigurationContext(self, "hitachi"),
        ]


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeHitachiOperatorCharm)

