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

"""Tests for encrypted storage enrollment and host mount management."""

import json
import os
import stat
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, mock_open

import charm
import encrypted_storage
import ops
import pytest

TARGET = "/dev/disk/by-id/wwn-0x5000c500aabbcc01"
SECRET_ID = "secret:cjk5slcrl3uc767oebp0"
MAPPER_PATH = "/dev/mapper/crypt-a1b2c3d4"


@pytest.fixture()
def started_harness(harness):
    """Begin the hypervisor charm harness."""
    harness.begin()
    return harness


def _add_relation(harness):
    """Add the Vaultlocker relation and its unit."""
    relation_id = harness.add_relation(
        "encrypted-device", "vaultlocker-hypervisor"
    )
    harness.add_relation_unit(relation_id, "vaultlocker-hypervisor/0")
    return relation_id


def _request_enrollment(harness, monkeypatch):
    """Run the action with a valid target and secret reference."""
    monkeypatch.setattr(
        encrypted_storage, "validate_target", lambda target: None
    )
    return harness.run_action(
        "configure-encrypted-storage",
        {"target": TARGET, "existing-key-secret-id": SECRET_ID},
    )


def _prepare_result(harness, monkeypatch):
    """Provide a verified result and an inactive Nova service."""
    monkeypatch.setattr(
        encrypted_storage, "validate_device_result", lambda *args: None
    )
    monkeypatch.setattr(
        encrypted_storage, "prepare_encrypted_storage", lambda *args: True
    )
    hypervisor_snap = MagicMock()
    hypervisor_snap.services = {"nova-compute": {"active": False}}
    monkeypatch.setattr(
        harness.charm,
        "get_snap_cache",
        lambda: {"openstack-hypervisor": hypervisor_snap},
    )
    return hypervisor_snap


class TestConfigureEncryptedStorageAction:
    """Tests for the enrollment action and relation result handler."""

    def test_fails_without_relation(self, started_harness):
        """An action requires the encrypted-device relation."""
        with pytest.raises(ops.testing.ActionFailed, match="not available"):
            started_harness.run_action(
                "configure-encrypted-storage",
                {"target": TARGET, "existing-key-secret-id": SECRET_ID},
            )

    def test_fails_on_invalid_target(self, started_harness, caplog):
        """A relative device path is rejected before publishing a request."""
        _add_relation(started_harness)
        with pytest.raises(ops.testing.ActionFailed, match="absolute"):
            started_harness.run_action(
                "configure-encrypted-storage",
                {"target": "relative", "existing-key-secret-id": SECRET_ID},
            )
        assert (
            "Encrypted storage action failed for target relative: "
            "target must be an absolute device path"
        ) in caplog.text

    def test_action_publishes_request_without_mounting(
        self, started_harness, monkeypatch
    ):
        """The action returns after publishing the enrollment request."""
        relation_id = _add_relation(started_harness)
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )

        output = _request_enrollment(started_harness, monkeypatch)

        assert output.results["status"] == "requested"
        assert output.results["target"] == TARGET
        assert output.results["message"] == "Vaultlocker enrollment requested"
        assert json.loads(
            started_harness.get_relation_data(
                relation_id, started_harness.charm.unit.name
            )["device_requests"]
        ) == {TARGET: {"existing_key_secret_id": SECRET_ID}}
        assert (
            started_harness.charm._encrypted_storage_status.status.name
            == "waiting"
        )
        mount.assert_not_called()

    def test_result_mounts_storage(self, started_harness, monkeypatch):
        """A later Vaultlocker result triggers the host mount."""
        relation_id = _add_relation(started_harness)
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        mount.assert_called_once_with(MAPPER_PATH)
        hypervisor_snap.stop.assert_not_called()
        hypervisor_snap.start.assert_called_once_with(["nova-compute"])
        assert (
            started_harness.charm._encrypted_storage_status.status.name
            == "active"
        )
        assert (
            started_harness.charm._state.encrypted_storage_result[
                "mapper_path"
            ]
            == MAPPER_PATH
        )

    def test_repeated_action_verifies_running_mount(
        self, started_harness, monkeypatch
    ):
        """A repeated result does not remount beneath running Nova."""
        relation_id = _add_relation(started_harness)
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True

        def stop_nova(services):
            """Record that the snap service stopped."""
            hypervisor_snap.services["nova-compute"]["active"] = False

        hypervisor_snap.stop.side_effect = stop_nova
        prepare = MagicMock(side_effect=[True, False])
        monkeypatch.setattr(
            encrypted_storage, "prepare_encrypted_storage", prepare
        )
        _request_enrollment(started_harness, monkeypatch)
        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )
        hypervisor_snap.services["nova-compute"]["active"] = True
        output = started_harness.run_action(
            "configure-encrypted-storage",
            {"target": TARGET, "existing-key-secret-id": SECRET_ID},
        )

        assert output.results["status"] == "completed"
        mount.assert_called_once_with(MAPPER_PATH)
        assert prepare.call_count == 2
        hypervisor_snap.stop.assert_called_once_with(["nova-compute"])
        hypervisor_snap.start.assert_called_once_with(["nova-compute"])

    def test_mount_failure_blocks_workload_status(
        self, started_harness, monkeypatch, caplog
    ):
        """A mount error appears in the charm's workload status."""
        relation_id = _add_relation(started_harness)
        monkeypatch.setattr(
            encrypted_storage,
            "mount_encrypted_storage",
            MagicMock(
                side_effect=encrypted_storage.EncryptedStorageError(
                    "mount failed"
                )
            ),
        )
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True

        def stop_nova(services):
            """Record that the snap service stopped."""
            hypervisor_snap.services["nova-compute"]["active"] = False

        hypervisor_snap.stop.side_effect = stop_nova
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        status = started_harness.charm._encrypted_storage_status.status
        assert status.name == "blocked"
        assert (
            status.message
            == "Encrypted storage operation failed"
        )
        assert (
            f"Encrypted storage configuration failed for target {TARGET}: "
            "mount failed"
        ) in caplog.text
        hypervisor_snap.stop.assert_called_once_with(["nova-compute"])
        hypervisor_snap.start.assert_not_called()

    def test_start_failure_blocks_workload_status(
        self, started_harness, monkeypatch
    ):
        """A failed nova-compute start is reported after the mount succeeds."""
        relation_id = _add_relation(started_harness)
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.start.side_effect = charm.snap.SnapError(
            "start failed"
        )
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        status = started_harness.charm._encrypted_storage_status.status
        assert status.name == "blocked"
        assert status.message == "nova-compute could not be started"
        mount.assert_called_once_with(MAPPER_PATH)
        assert (
            started_harness.charm._state.encrypted_storage_result[
                "mapper_path"
            ]
            == MAPPER_PATH
        )

    def test_running_nova_is_stopped_before_mount(
        self, started_harness, monkeypatch
    ):
        """Mounting follows the local nova-compute stop."""
        relation_id = _add_relation(started_harness)
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True
        calls = []

        def prepare_storage(mapper_path):
            """Record that fstab preparation precedes the stop."""
            calls.append("prepare")
            return True

        def stop_nova(services):
            """Record that the snap service stopped."""
            calls.append("stop")
            hypervisor_snap.services["nova-compute"]["active"] = False

        def mount_storage(mapper_path):
            """Record that mounting follows the stop."""
            calls.append("mount")

        hypervisor_snap.stop.side_effect = stop_nova
        hypervisor_snap.start.side_effect = lambda services: calls.append(
            "start"
        )
        monkeypatch.setattr(
            encrypted_storage, "prepare_encrypted_storage", prepare_storage
        )
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount_storage
        )
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        status = started_harness.charm._encrypted_storage_status.status
        assert status.name == "active"
        assert calls == ["prepare", "stop", "mount", "start"]
        hypervisor_snap.start.assert_called_once_with(["nova-compute"])

    def test_stop_failure_blocks_mount(self, started_harness, monkeypatch):
        """A failed service stop leaves the storage unmounted."""
        relation_id = _add_relation(started_harness)
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True
        hypervisor_snap.stop.side_effect = charm.snap.SnapError("stop failed")
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        status = started_harness.charm._encrypted_storage_status.status
        assert status.name == "blocked"
        assert status.message == "nova-compute could not be stopped"
        mount.assert_not_called()
        hypervisor_snap.start.assert_not_called()

    def test_running_nova_blocks_mount_if_stop_does_not_take_effect(
        self, started_harness, monkeypatch
    ):
        """Mounting requires an inactive nova-compute service."""
        relation_id = _add_relation(started_harness)
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        _request_enrollment(started_harness, monkeypatch)

        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )

        status = started_harness.charm._encrypted_storage_status.status
        assert status.name == "blocked"
        assert status.message == "nova-compute is still running"
        mount.assert_not_called()
        hypervisor_snap.start.assert_not_called()

    def test_failed_mount_can_be_retried(self, started_harness, monkeypatch):
        """A retry mounts storage and restarts Nova after a mount failure."""
        relation_id = _add_relation(started_harness)
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        hypervisor_snap.services["nova-compute"]["active"] = True

        def stop_nova(services):
            """Record that the snap service stopped."""
            hypervisor_snap.services["nova-compute"]["active"] = False

        hypervisor_snap.stop.side_effect = stop_nova
        mount = MagicMock(
            side_effect=encrypted_storage.EncryptedStorageError("mount failed")
        )
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        _request_enrollment(started_harness, monkeypatch)
        started_harness.update_relation_data(
            relation_id,
            "vaultlocker-hypervisor/0",
            {
                "device_results": json.dumps(
                    {
                        TARGET: {
                            "mapper_path": MAPPER_PATH,
                            "luks_uuid": "a1b2c3d4",
                        }
                    }
                )
            },
        )
        mount.side_effect = None

        output = started_harness.run_action(
            "configure-encrypted-storage",
            {"target": TARGET, "existing-key-secret-id": SECRET_ID},
        )

        assert output.results["status"] == "completed"
        hypervisor_snap.stop.assert_called_once_with(["nova-compute"])
        hypervisor_snap.start.assert_called_once_with(["nova-compute"])

    def test_saved_result_can_be_mounted_without_relation(
        self, started_harness, monkeypatch
    ):
        """An enrolled device can be mounted after the relation is lost."""
        relation_id = _add_relation(started_harness)
        hypervisor_snap = _prepare_result(started_harness, monkeypatch)
        mount = MagicMock()
        monkeypatch.setattr(
            encrypted_storage, "mount_encrypted_storage", mount
        )
        _request_enrollment(started_harness, monkeypatch)
        started_harness.charm._state.encrypted_storage_result = {
            "target": TARGET,
            "mapper_path": MAPPER_PATH,
            "luks_uuid": "a1b2c3d4",
        }
        started_harness.remove_relation(relation_id)

        output = started_harness.run_action(
            "configure-encrypted-storage",
            {"target": TARGET, "existing-key-secret-id": SECRET_ID},
        )

        assert output.results["status"] == "completed"
        mount.assert_called_once_with(MAPPER_PATH)
        hypervisor_snap.start.assert_called_once_with(["nova-compute"])


def test_mapper_requires_xfs(tmp_path, monkeypatch):
    """A non-XFS mapper is rejected before fstab is changed."""
    instances_path = tmp_path / "instances"
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("/dev/sda1 / ext4 defaults 0 1\n")
    original_fstab = fstab_path.read_text()
    real_stat = Path.stat

    def stat_mapper(self, *args, **kwargs):
        if str(self) == MAPPER_PATH:
            return MagicMock(st_mode=stat.S_IFBLK, st_rdev=123)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(
        Path,
        "stat",
        stat_mapper,
    )
    monkeypatch.setattr(
        encrypted_storage.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, "ext4\n", ""),
    )

    with pytest.raises(encrypted_storage.EncryptedStorageError, match="XFS"):
        encrypted_storage.prepare_encrypted_storage(
            MAPPER_PATH, instances_path, fstab_path
        )
    assert fstab_path.read_text() == original_fstab


def test_target_requires_block_device(tmp_path):
    """A regular file cannot be selected for encrypted storage."""
    target = tmp_path / "regular-file"
    target.write_text("data")

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="not a block device"
    ):
        encrypted_storage.validate_target(str(target))


def test_result_rejects_mismatched_luks_uuid(monkeypatch):
    """A Vaultlocker result must match the selected LUKS device UUID."""
    monkeypatch.setattr(
        encrypted_storage, "validate_target", lambda target: None
    )
    monkeypatch.setattr(
        encrypted_storage.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, "TYPE=crypto_LUKS\nUUID=different\n", ""
        ),
    )

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="LUKS UUID"
    ):
        encrypted_storage.validate_device_result(
            TARGET, MAPPER_PATH, "a1b2c3d4"
        )


@pytest.mark.parametrize("backing_device", ["8:1", "8:2"])
def test_result_checks_mapper_backing_device(monkeypatch, backing_device):
    """The unlocked mapper must be backed by the selected LUKS device."""
    target_device = os.makedev(8, 1)
    mapper_device = os.makedev(253, 0)
    real_stat = os.stat

    def stat_device(path, *args, **kwargs):
        if path == TARGET:
            return MagicMock(st_rdev=target_device)
        if path == MAPPER_PATH:
            return MagicMock(st_rdev=mapper_device)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(
        encrypted_storage, "validate_target", lambda target: None
    )
    monkeypatch.setattr(encrypted_storage.os, "stat", stat_device)
    listdir = MagicMock(return_value=["sdb1"])
    monkeypatch.setattr(encrypted_storage.os, "listdir", listdir)
    device_file = mock_open(read_data=f"{backing_device}\n")
    monkeypatch.setattr(encrypted_storage, "open", device_file, raising=False)
    monkeypatch.setattr(
        encrypted_storage.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, "TYPE=crypto_LUKS\nUUID=a1b2c3d4\n", ""
        ),
    )

    if backing_device == "8:1":
        encrypted_storage.validate_device_result(
            TARGET, MAPPER_PATH, "a1b2c3d4"
        )
    else:
        with pytest.raises(
            encrypted_storage.EncryptedStorageError,
            match="does not map target",
        ):
            encrypted_storage.validate_device_result(
                TARGET, MAPPER_PATH, "a1b2c3d4"
            )

    listdir.assert_called_once_with("/sys/dev/block/253:0/slaves")
    device_file.assert_called_once_with("/sys/dev/block/253:0/slaves/sdb1/dev")


def test_mounts_empty_instances_path(tmp_path, monkeypatch):
    """The mount helpers write fstab, mount the mapper, and verify the result."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("/dev/sda1 / ext4 defaults 0 1")
    monkeypatch.setattr(
        encrypted_storage,
        "_mapper_device",
        lambda path: instances_path.stat().st_dev,
    )
    mount_states = iter([False, False, True])
    monkeypatch.setattr(os.path, "ismount", lambda path: next(mount_states))
    run = MagicMock(return_value=CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(encrypted_storage.subprocess, "run", run)

    assert encrypted_storage.prepare_encrypted_storage(
        MAPPER_PATH, instances_path, fstab_path
    )
    encrypted_storage.mount_encrypted_storage(
        MAPPER_PATH, instances_path, fstab_path
    )

    assert (
        f"{MAPPER_PATH} {instances_path} xfs defaults,nofail"
        in fstab_path.read_text()
    )
    assert "0 1\n" in fstab_path.read_text()
    run.assert_called_once_with(
        ["mount", str(instances_path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=encrypted_storage.MOUNT_TIMEOUT_SECONDS,
    )


def test_conflicting_fstab_entry_is_rejected(tmp_path, monkeypatch):
    """An existing mount definition for another device is preserved."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text(f"/dev/sdb1 {instances_path} xfs defaults 0 2\n")
    monkeypatch.setattr(encrypted_storage, "_mapper_device", lambda path: 123)
    monkeypatch.setattr(os.path, "ismount", lambda path: False)

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="different fstab"
    ):
        encrypted_storage.prepare_encrypted_storage(
            MAPPER_PATH, instances_path, fstab_path
        )
    assert fstab_path.read_text().startswith("/dev/sdb1")


def test_preparation_skips_already_mounted_storage(tmp_path, monkeypatch):
    """Preparation reports that an existing matching mount needs no mount call."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    fstab_path = tmp_path / "fstab"
    entry = f"{MAPPER_PATH} {instances_path} xfs defaults,nofail 0 2\n"
    fstab_path.write_text(entry)
    monkeypatch.setattr(
        encrypted_storage,
        "_mapper_device",
        lambda path: instances_path.stat().st_dev,
    )
    monkeypatch.setattr(os.path, "ismount", lambda path: True)

    assert not encrypted_storage.prepare_encrypted_storage(
        MAPPER_PATH, instances_path, fstab_path
    )

    assert fstab_path.read_text() == entry


def test_existing_instance_data_is_not_hidden(tmp_path, monkeypatch):
    """A populated local instances directory blocks the mount."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    (instances_path / "existing-instance").write_text("data")
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("")
    monkeypatch.setattr(encrypted_storage, "_mapper_device", lambda path: 123)
    monkeypatch.setattr(os.path, "ismount", lambda path: False)

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="contains data"
    ):
        encrypted_storage.prepare_encrypted_storage(
            MAPPER_PATH, instances_path, fstab_path
        )
    assert fstab_path.read_text() == ""


def test_wrong_mounted_device_is_rejected(tmp_path, monkeypatch):
    """A writable mount from another device does not count as success."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("")
    monkeypatch.setattr(
        encrypted_storage,
        "_mapper_device",
        lambda path: instances_path.stat().st_dev + 1,
    )
    monkeypatch.setattr(os.path, "ismount", lambda path: True)

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="different device"
    ):
        encrypted_storage.prepare_encrypted_storage(
            MAPPER_PATH, instances_path, fstab_path
        )
    assert fstab_path.read_text() == ""


def test_mount_rechecks_instances_path_after_prepare(tmp_path, monkeypatch):
    """Files created before Nova stops cannot be hidden by the mount."""
    instances_path = tmp_path / "instances"
    instances_path.mkdir()
    fstab_path = tmp_path / "fstab"
    fstab_path.write_text("")
    monkeypatch.setattr(encrypted_storage, "_mapper_device", lambda path: 123)
    monkeypatch.setattr(os.path, "ismount", lambda path: False)
    run = MagicMock()
    monkeypatch.setattr(encrypted_storage.subprocess, "run", run)

    assert encrypted_storage.prepare_encrypted_storage(
        MAPPER_PATH, instances_path, fstab_path
    )
    (instances_path / "existing-instance").write_text("data")

    with pytest.raises(
        encrypted_storage.EncryptedStorageError, match="contains data"
    ):
        encrypted_storage.mount_encrypted_storage(
            MAPPER_PATH, instances_path, fstab_path
        )
    run.assert_not_called()
