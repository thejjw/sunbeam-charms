#!/usr/bin/env python3

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

"""Manage the host mount for encrypted Nova instance storage."""

import os
import stat
import subprocess
import tempfile
from pathlib import (
    Path,
)

INSTANCES_PATH = Path(
    "/var/snap/openstack-hypervisor/common/lib/nova/instances"
)
FSTAB_PATH = Path("/etc/fstab")
FSTAB_MARKER = "# managed-by-openstack-hypervisor"
BLKID_TIMEOUT_SECONDS = 30
MOUNT_TIMEOUT_SECONDS = 60


class EncryptedStorageError(Exception):
    """An encrypted storage mount cannot be configured or verified."""

    def __init__(self, message: str, status_message: str | None = None):
        """Keep the full error for logs and a short reason for status."""
        super().__init__(message)
        self.status_message = (
            status_message or "Encrypted storage operation failed"
        )


def validate_target(target: str) -> None:
    """Check that a device path exists and refers to a block device."""
    if (
        not isinstance(target, str)
        or not target.startswith("/")
        or "\x00" in target
    ):
        raise EncryptedStorageError(
            "target must be an absolute device path",
            "Target is not an absolute device path",
        )
    if not os.path.exists(target):
        raise EncryptedStorageError(
            f"target {target} does not exist",
            "Selected storage device does not exist",
        )
    try:
        mode = os.stat(target).st_mode
    except OSError as exc:
        raise EncryptedStorageError(
            f"unable to inspect target {target}: {exc}",
            "Selected storage device cannot be inspected",
        ) from exc
    if not stat.S_ISBLK(mode):
        raise EncryptedStorageError(
            f"target {target} is not a block device",
            "Target is not a block device",
        )


def validate_device_result(
    target: str, mapper_path: str, luks_uuid: str
) -> None:
    """Verify that the mapper belongs to the enrolled LUKS device."""
    validate_target(target)
    validate_target(mapper_path)

    try:
        process = subprocess.run(
            ["blkid", "--probe", "--output", "export", target],
            capture_output=True,
            text=True,
            check=True,
            timeout=BLKID_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EncryptedStorageError(
            f"unable to inspect LUKS device {target}: {exc}",
            "Selected LUKS device cannot be inspected",
        ) from exc
    properties = dict(
        line.split("=", 1)
        for line in process.stdout.splitlines()
        if "=" in line
    )
    if properties.get("TYPE") != "crypto_LUKS":
        raise EncryptedStorageError(
            f"target {target} is not LUKS-encrypted",
            "Target is not LUKS-encrypted",
        )
    if properties.get("UUID") != luks_uuid:
        raise EncryptedStorageError(
            f"target {target} does not match the enrolled LUKS UUID",
            "Target LUKS UUID differs from enrollment",
        )

    # Device-mapper exposes its backing devices in sysfs. Comparing device
    # numbers also supports stable symlinks, partitions and LVs.
    try:
        target_device = os.stat(target).st_rdev
        mapper_device = os.stat(mapper_path).st_rdev
        slaves_path = (
            f"/sys/dev/block/{os.major(mapper_device)}:"
            f"{os.minor(mapper_device)}/slaves"
        )
        backing_devices = []
        for name in os.listdir(slaves_path):
            with open(os.path.join(slaves_path, name, "dev")) as device:
                backing_devices.append(device.read().strip())
    except OSError as exc:
        raise EncryptedStorageError(
            f"unable to inspect mapper {mapper_path}: {exc}",
            "Vaultlocker mapper cannot be inspected",
        ) from exc
    expected_device = f"{os.major(target_device)}:{os.minor(target_device)}"
    if backing_devices != [expected_device]:
        raise EncryptedStorageError(
            f"mapper {mapper_path} does not map target {target}",
            "Vaultlocker mapper does not match target",
        )


def _mapper_device(mapper_path: Path) -> int:
    """Validate the unlocked mapper and return its device number."""
    try:
        info = mapper_path.stat()
    except OSError as exc:
        raise EncryptedStorageError(
            f"cannot inspect mapper {mapper_path}: {exc}",
            "Vaultlocker mapper cannot be inspected",
        ) from exc
    if not stat.S_ISBLK(info.st_mode):
        raise EncryptedStorageError(
            f"mapper {mapper_path} is not a block device",
            "Vaultlocker mapper is not a block device",
        )

    try:
        result = subprocess.run(
            ["blkid", "-o", "value", "-s", "TYPE", str(mapper_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=BLKID_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EncryptedStorageError(
            f"cannot inspect filesystem on {mapper_path}: {exc}",
            "Unlocked filesystem cannot be inspected",
        ) from exc
    if result.returncode != 0 or result.stdout.strip() != "xfs":
        raise EncryptedStorageError(
            f"mapper {mapper_path} must contain an XFS filesystem",
            "Unlocked device does not contain XFS",
        )
    return info.st_rdev


def _matching_fstab_entry(
    mapper_path: Path, instances_path: Path, fstab_path: Path
) -> tuple[bool, str]:
    """Find a matching mount definition without changing fstab."""
    try:
        content = fstab_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EncryptedStorageError(
            f"cannot read {fstab_path}: {exc}",
            "/etc/fstab cannot be read",
        ) from exc

    for line in content.splitlines():
        fields = line.strip().split()
        if not fields or fields[0].startswith("#") or len(fields) < 2:
            continue
        if Path(fields[1]) != instances_path:
            continue
        if (
            len(fields) < 3
            or Path(fields[0]) != mapper_path
            or fields[2] != "xfs"
        ):
            raise EncryptedStorageError(
                f"{instances_path} already has a different fstab entry",
                "Nova instances fstab entry conflicts",
            )
        return True, content

    return False, content


def _ensure_fstab_entry(
    mapper_path: Path, instances_path: Path, fstab_path: Path
) -> None:
    """Add a host fstab entry without replacing an existing mount definition."""
    exists, content = _matching_fstab_entry(
        mapper_path, instances_path, fstab_path
    )
    if exists:
        return

    entry = f"{mapper_path} {instances_path} xfs defaults,nofail 0 2 {FSTAB_MARKER}\n"
    try:
        with fstab_path.open("a", encoding="utf-8") as fstab:
            if content and not content.endswith("\n"):
                fstab.write("\n")
            fstab.write(entry)
    except OSError as exc:
        raise EncryptedStorageError(
            f"cannot update {fstab_path}: {exc}",
            "/etc/fstab cannot be updated",
        ) from exc


def _verify_mount(instances_path: Path, mapper_device: int) -> None:
    """Check that the instances path uses the mapper and is writable."""
    if not os.path.ismount(instances_path):
        raise EncryptedStorageError(
            f"{instances_path} is not mounted",
            "Nova instances path is not mounted",
        )
    try:
        mounted_device = instances_path.stat().st_dev
        if mounted_device != mapper_device:
            raise EncryptedStorageError(
                f"{instances_path} is mounted from a different device",
                "Nova instances path is mounted from another device",
            )
        with tempfile.TemporaryFile(dir=instances_path):
            pass
    except OSError as exc:
        raise EncryptedStorageError(
            f"{instances_path} is not writable: {exc}",
            "Nova instances mount is not writable",
        ) from exc


def _ensure_empty_instances_path(instances_path: Path) -> None:
    """Prepare an unmounted instances directory without hiding existing data."""
    try:
        if instances_path.exists() and any(instances_path.iterdir()):
            raise EncryptedStorageError(
                f"{instances_path} contains data; mounting encrypted storage would hide it",
                "Nova instances path contains data",
            )
        instances_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EncryptedStorageError(
            f"cannot prepare {instances_path}: {exc}",
            "Nova instances directory cannot be prepared",
        ) from exc


def prepare_encrypted_storage(
    mapper_path: str,
    instances_path: Path = INSTANCES_PATH,
    fstab_path: Path = FSTAB_PATH,
) -> bool:
    """Validate the XFS mapper and fstab entry; return whether a mount is needed."""
    mapper = Path(mapper_path)
    mapper_device = _mapper_device(mapper)

    mount_needed = not os.path.ismount(instances_path)
    if mount_needed:
        _ensure_empty_instances_path(instances_path)
    else:
        _verify_mount(instances_path, mapper_device)

    _ensure_fstab_entry(mapper, instances_path, fstab_path)
    return mount_needed


def mount_encrypted_storage(
    mapper_path: str,
    instances_path: Path = INSTANCES_PATH,
    fstab_path: Path = FSTAB_PATH,
) -> None:
    """Mount the prepared XFS mapper at the Nova instances path."""
    mapper = Path(mapper_path)
    mapper_device = _mapper_device(mapper)
    exists, _ = _matching_fstab_entry(mapper, instances_path, fstab_path)
    if not exists:
        raise EncryptedStorageError(
            f"{instances_path} has no fstab entry",
            "Nova instances path has no fstab entry",
        )
    if not os.path.ismount(instances_path):
        _ensure_empty_instances_path(instances_path)
        try:
            result = subprocess.run(
                ["mount", str(instances_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=MOUNT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise EncryptedStorageError(
                f"cannot mount {instances_path}: {exc}",
                "Nova instances mount failed",
            ) from exc
        if result.returncode != 0:
            raise EncryptedStorageError(
                f"cannot mount {instances_path}: {result.stderr.strip()}",
                "Nova instances mount failed",
            )

    _verify_mount(instances_path, mapper_device)
