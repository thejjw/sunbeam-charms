# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manage the address-only Netplan fragment for NDR peering ports."""

import ipaddress
import logging
import re
import subprocess
import tempfile
from pathlib import (
    Path,
)

import yaml

logger = logging.getLogger(__name__)

NETPLAN_PATH = Path("/etc/netplan/90-sunbeam-ndr.yaml")
MANAGED_HEADER = "# Managed by the openstack-network-agents charm for NDR.\n"
PEERING_PORT = re.compile(r"ndr-[a-z0-9-]{1,11}")


class NetworkConfigurationError(subprocess.SubprocessError):
    """Safe details about a failed Netplan or networkd operation."""


def validate_addresses(addresses: object) -> dict[str, str]:
    """Validate managed interface names and addresses returned by the snap."""
    if not isinstance(addresses, dict):
        raise ValueError(
            "Expected a mapping of managed NDR peering addresses."
        )
    for interface, address in addresses.items():
        if not (
            isinstance(interface, str)
            and PEERING_PORT.fullmatch(interface)
            and isinstance(address, str)
            and "/" in address
        ):
            raise ValueError(
                "Invalid managed NDR peering interface or address."
            )
        ipaddress.ip_interface(address)
    return addresses


def managed_fragment(path: Path, required: bool) -> str | None:
    """Return the current fragment while preserving anything not charm-owned."""
    if path.is_symlink():
        if required:
            raise ValueError(f"Refusing to replace symlink {path}.")
        return None
    try:
        contents = path.read_text()
    except FileNotFoundError:
        return None
    if not contents.startswith(MANAGED_HEADER):
        if required:
            raise ValueError(
                f"Refusing to replace operator-owned Netplan file {path}."
            )
        return None
    return contents


def reconcile_addresses(path: Path, addresses: dict[str, str]) -> None:
    """Update the managed fragment and reconfigure only changed peering ports."""
    previous = managed_fragment(path, required=bool(addresses))
    ethernets = {
        name: {
            "match": {"name": name},
            "renderer": "networkd",
            "accept-ra": False,
            "addresses": [address],
        }
        for name, address in addresses.items()
    }
    desired = (
        MANAGED_HEADER
        + yaml.safe_dump({"network": {"version": 2, "ethernets": ethernets}})
        if ethernets
        else None
    )
    if previous == desired:
        return
    previous_links = (
        yaml.safe_load(previous)["network"]["ethernets"] if previous else {}
    )
    changed = sorted(
        name
        for name in ethernets
        if ethernets[name] != previous_links.get(name)
    )
    _write_fragment(path, desired)
    try:
        _reload_networkd()
        if changed:
            _run("networkctl", "reconfigure", *changed)
    except (OSError, subprocess.SubprocessError):
        try:
            _write_fragment(path, previous)
            _reload_networkd()
        except (OSError, subprocess.SubprocessError):
            logger.exception("Failed to restore NDR Netplan configuration.")
        raise


def _reload_networkd() -> None:
    """Regenerate and load the networkd configuration from Netplan."""
    _run("netplan", "generate")
    _run("networkctl", "reload")


def _run(*argv: str) -> None:
    """Report command failures without exposing host configuration."""
    # Callers supply fixed commands and validated NDR interface names.
    operation = " ".join(argv)
    # Rollback logs tracebacks, so suppress exception chaining to keep raw
    # subprocess and OS exceptions out of the logs.
    try:
        subprocess.run(
            argv,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except subprocess.CalledProcessError as error:
        raise NetworkConfigurationError(
            f"{operation} failed with exit status {error.returncode}."
        ) from None
    except subprocess.TimeoutExpired as error:
        raise NetworkConfigurationError(
            f"{operation} timed out after {error.timeout} seconds."
        ) from None
    except OSError as error:
        raise NetworkConfigurationError(
            f"{operation} could not start ({type(error).__name__})."
        ) from None


def _write_fragment(path: Path, contents: str | None) -> None:
    """Replace only the charm-owned fragment, with private file permissions."""
    if contents is None:
        path.unlink(missing_ok=True)
        return
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False
    ) as output:
        temp = Path(output.name)
        try:
            output.write(contents)
            output.flush()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
