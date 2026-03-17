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

"""Shared fixtures for openstack-network-agents unit tests (ops.testing)."""

from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
    mock_open,
)

import charm
import pytest
import yaml
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]

FAKE_NODE_NAME = "juju-test-0"


def make_microovn_status(
    node_name: str = FAKE_NODE_NAME,
    services: str = "central, chassis, switch",
) -> str:
    """Build a ``microovn status`` output string."""
    return (
        "MicroOVN deployment summary:\n"
        f"- {node_name} (10.0.0.1)\n"
        f"  Services: {services}\n"
    )


def make_daemon_yaml(node_name: str = FAKE_NODE_NAME) -> str:
    """Build daemon.yaml content."""
    return yaml.dump({"name": node_name})


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment.

    Replaces snap, subprocess, and the daemon.yaml file read so charm
    code does not touch the real system.
    """
    # snap module
    mock_snap = MagicMock()
    mock_snap.SnapError = Exception
    mock_snap.SnapNotFoundError = Exception
    mock_snap.SnapState.Latest = "latest"
    network_agents_snap = MagicMock()
    network_agents_snap.present = False
    network_agents_snap.get.return_value = {}
    microovn_snap = MagicMock()
    microovn_snap.present = True
    mock_snap.SnapCache.return_value = {
        "openstack-network-agents": network_agents_snap,
        "microovn": microovn_snap,
    }
    mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(charm, "snap", mock_snap)
    monkeypatch.setattr(
        charm.OpenstackNetworkAgentsOperatorCharm,
        "_import_snap",
        lambda self: mock_snap,
    )

    # subprocess — default microovn status shows all services
    mock_subprocess = MagicMock()
    status_result = MagicMock()
    status_result.returncode = 0
    status_result.stdout = make_microovn_status()
    status_result.stderr = ""
    mock_subprocess.run.return_value = status_result
    mock_subprocess.TimeoutExpired = type("TimeoutExpired", (Exception,), {})
    monkeypatch.setattr(charm, "subprocess", mock_subprocess)

    # daemon.yaml — patch builtins.open for the daemon.yaml path
    _real_open = open
    daemon_yaml_content = make_daemon_yaml()

    def _patched_open(path, *args, **kwargs):
        if str(path) == charm.MICROOVN_DAEMON_YAML:
            return mock_open(read_data=daemon_yaml_content)()
        return _real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _patched_open)


@pytest.fixture()
def ctx():
    """Create a testing.Context for OpenstackNetworkAgentsOperatorCharm."""
    return testing.Context(
        charm.OpenstackNetworkAgentsOperatorCharm,
        charm_root=CHARM_ROOT,
    )


def juju_info_relation() -> testing.SubordinateRelation:
    """juju-info subordinate relation (mandatory)."""
    return testing.SubordinateRelation(
        endpoint="juju-info",
        remote_app_name="principal-app",
    )


@pytest.fixture()
def complete_relations():
    """All mandatory relations needed to reach active status."""
    return [juju_info_relation()]


@pytest.fixture()
def complete_state(complete_relations):
    """Full state with leader and all mandatory relations."""
    return testing.State(
        leader=True,
        relations=complete_relations,
    )
