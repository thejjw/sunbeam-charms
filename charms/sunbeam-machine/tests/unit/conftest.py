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

"""Shared fixtures for sunbeam-machine unit tests (ops.testing)."""

from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
    mock_open,
)

import charm
import pytest
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture()
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment.

    Covers: sysctl, apt, Path, socket, platform, builtins.open.
    """
    # sysctl
    mock_sysctl = MagicMock()
    monkeypatch.setattr(charm, "sysctl", mock_sysctl)

    # apt
    mock_apt = MagicMock()
    mock_pkg = MagicMock()
    mock_pkg.present = True
    mock_apt.DebianPackage.from_system.return_value = mock_pkg
    monkeypatch.setattr(charm, "apt", mock_apt)

    # Path – used for iSCSI initiator configuration
    mock_path_instance = MagicMock()
    mock_path_instance.exists.return_value = False
    mock_path_instance.parent.mkdir = MagicMock()
    mock_path_instance.touch = MagicMock()
    mock_path_instance.chmod = MagicMock()
    mock_path_instance.open = MagicMock(return_value=mock_open(read_data="")())
    mock_path_instance.__enter__ = MagicMock(return_value=mock_path_instance)
    mock_path_instance.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(
        charm, "Path", MagicMock(return_value=mock_path_instance)
    )

    # socket
    mock_socket = MagicMock()
    mock_socket.getfqdn.return_value = "test.local"
    monkeypatch.setattr(charm, "socket", mock_socket)

    # platform.release – used for kernel package name
    mock_platform = MagicMock()
    mock_platform.release.return_value = "6.8.0-generic"
    monkeypatch.setattr(charm, "platform", mock_platform)

    # builtins.open – used to read/write /etc/environment
    monkeypatch.setattr(
        "builtins.open",
        mock_open(read_data="PATH=FAKEPATH\n"),
    )


@pytest.fixture()
def ctx(_mock_heavy_externals):
    """Create a testing.Context for SunbeamMachineCharm."""
    return testing.Context(
        charm.SunbeamMachineCharm,
        charm_root=CHARM_ROOT,
    )
