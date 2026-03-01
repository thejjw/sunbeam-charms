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

"""Shared fixtures for epa-orchestrator unit tests (ops.testing)."""

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


@pytest.fixture(autouse=True)
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment."""
    mock_snap = MagicMock()
    mock_snap.SnapError = Exception
    mock_snap.SnapNotFoundError = Exception
    mock_snap.SnapState.Latest = "latest"
    epa_snap = MagicMock()
    epa_snap.present = False
    epa_snap.services = {}
    mock_snap.SnapCache.return_value = {
        "epa-orchestrator": epa_snap,
    }
    mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(charm, "snap", mock_snap)
    monkeypatch.setattr(
        charm.EpaOrchestratorCharm,
        "_import_snap",
        lambda self: mock_snap,
    )


@pytest.fixture()
def ctx():
    """Create a testing.Context for EpaOrchestratorCharm."""
    return testing.Context(
        charm.EpaOrchestratorCharm,
        charm_root=CHARM_ROOT,
    )


def sunbeam_machine_relation() -> testing.SubordinateRelation:
    """sunbeam-machine subordinate relation."""
    return testing.SubordinateRelation(
        endpoint="sunbeam-machine",
        remote_app_name="openstack-hypervisor",
        remote_unit_data={"ingress-address": "10.0.0.1"},
    )


@pytest.fixture()
def complete_relations():
    """All mandatory relations needed to reach active status."""
    return [sunbeam_machine_relation()]


@pytest.fixture()
def complete_state(complete_relations):
    """Full state with leader and all mandatory relations."""
    return testing.State(
        leader=True,
        relations=complete_relations,
    )
