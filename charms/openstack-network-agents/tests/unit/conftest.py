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
)

import charm
import pytest
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment.

    Replaces snap, subprocess so charm code does not touch the real system.
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
    microovn_snap.services = {"switch": {"active": True}}
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

    # subprocess
    mock_subprocess = MagicMock()
    monkeypatch.setattr(charm, "subprocess", mock_subprocess)


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
