# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the charm-owned Netplan fragment."""

import stat
import subprocess
from unittest.mock import (
    Mock,
    call,
)

import ndr_peering
import pytest
import yaml


def test_only_changed_ports_are_reconfigured(tmp_path, monkeypatch):
    """Install address-only config and avoid reconfiguration on reapply."""
    path = tmp_path / "ndr.yaml"
    other_file = tmp_path / "50-cloud-init.yaml"
    other_file.write_text("operator-owned")
    command = Mock()
    monkeypatch.setattr(ndr_peering, "_run", command)
    ndr_peering.reconcile_addresses(path, {"ndr-1a": "192.0.2.3/24"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert yaml.safe_load(path.read_text())["network"]["ethernets"] == {
        "ndr-1a": {
            "match": {"name": "ndr-1a"},
            "renderer": "networkd",
            "accept-ra": False,
            "addresses": ["192.0.2.3/24"],
        }
    }
    command.reset_mock()
    ndr_peering.reconcile_addresses(path, {"ndr-1a": "192.0.2.3/24"})
    command.assert_not_called()
    ndr_peering.reconcile_addresses(
        path, {"ndr-1a": "192.0.2.3/24", "ndr-2b": "2001:db8::2/64"}
    )
    command.assert_called_with("networkctl", "reconfigure", "ndr-2b")
    ndr_peering.reconcile_addresses(path, {})
    assert not path.exists()
    assert other_file.read_text() == "operator-owned"


def test_foreign_fragment_is_not_replaced_or_removed(tmp_path):
    """Refuse to write over a pre-existing config outside charm ownership."""
    path = tmp_path / "ndr.yaml"
    path.write_text("managed by operator")
    with pytest.raises(ValueError, match="operator-owned"):
        ndr_peering.reconcile_addresses(path, {"ndr-1a": "192.0.2.3/24"})
    ndr_peering.reconcile_addresses(path, {})
    assert path.read_text() == "managed by operator"


@pytest.mark.parametrize(
    "failed_command",
    [
        ("netplan", "generate"),
        ("networkctl", "reload"),
        ("networkctl", "reconfigure", "ndr-2b"),
    ],
)
def test_apply_failure_restores_and_reloads_previous_fragment(
    tmp_path, monkeypatch, failed_command
):
    """Restore the previous YAML and regenerate the networkd configuration."""
    path = tmp_path / "ndr.yaml"
    apply = Mock()
    monkeypatch.setattr(ndr_peering, "_run", apply)
    ndr_peering.reconcile_addresses(path, {"ndr-1a": "192.0.2.3/24"})
    previous = path.read_text()
    apply.reset_mock()
    failure = subprocess.CalledProcessError(1, failed_command)

    failed = False

    def fail_once(*args):
        nonlocal failed
        if failed:
            assert path.read_text() == previous
        elif args == failed_command:
            failed = True
            raise failure

    apply.side_effect = fail_once
    with pytest.raises(subprocess.CalledProcessError) as error:
        ndr_peering.reconcile_addresses(
            path, {"ndr-1a": "192.0.2.3/24", "ndr-2b": "2001:db8::2/64"}
        )

    assert error.value is failure
    assert path.read_text() == previous
    reload = [call("netplan", "generate"), call("networkctl", "reload")]
    apply_commands = [*reload, call("networkctl", "reconfigure", "ndr-2b")]
    assert apply.call_args_list == [
        *apply_commands[: apply_commands.index(call(*failed_command)) + 1],
        *reload,
    ]


def test_rollback_failure_preserves_original_apply_error(
    tmp_path, monkeypatch, caplog
):
    """Log rollback failure and report the original error to the caller."""
    path = tmp_path / "ndr.yaml"
    failure = subprocess.CalledProcessError(1, ["networkctl", "reconfigure"])
    rollback_failure = subprocess.CalledProcessError(
        1, ["netplan", "generate"]
    )
    apply = Mock(side_effect=[None, None, failure, rollback_failure])
    monkeypatch.setattr(ndr_peering, "_run", apply)

    with pytest.raises(subprocess.CalledProcessError) as error:
        ndr_peering.reconcile_addresses(path, {"ndr-1a": "192.0.2.3/24"})

    assert error.value is failure
    assert not path.exists()
    assert "Failed to restore NDR Netplan configuration" in caplog.text
