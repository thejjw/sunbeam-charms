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

"""Shared fixtures for sunbeam-clusterd unit tests."""

from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
    patch,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _mock_clusterd():
    """Mock clusterd client so no real unix socket is required."""
    mock_client = MagicMock()
    mock_client.ready.return_value = True
    mock_client.bootstrap.return_value = None
    mock_client.generate_token.return_value = "fake-token"
    mock_client.get_members.return_value = [
        {"name": "sunbeam-clusterd-0", "role": "voter"}
    ]
    mock_client.get_member.return_value = {
        "name": "sunbeam-clusterd-0",
        "role": "voter",
    }
    mock_client.set_certs.return_value = None
    mock_client.shutdown.return_value = None
    with patch("clusterd.ClusterdClient", return_value=mock_client):
        yield mock_client


@pytest.fixture(autouse=True)
def _mock_snap():
    """Mock snap module so no real snap operations occur."""
    mock_openstack = MagicMock()
    mock_openstack.present = True
    mock_openstack.channel = "2026.1/edge"
    mock_openstack.revision = "100"
    mock_openstack.get.return_value = None

    mock_cache = MagicMock()
    mock_cache.__getitem__ = MagicMock(return_value=mock_openstack)

    with patch("charm.snap.SnapCache", return_value=mock_cache):
        yield mock_cache


@pytest.fixture()
def ctx():
    """Create a testing.Context for SunbeamClusterdCharm."""
    return testing.Context(charm.SunbeamClusterdCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def peers():
    """Peer relation for clusterd."""
    return peer_relation()


@pytest.fixture()
def complete_relations(peers):
    """All relations needed to reach active status."""
    return [peers]


@pytest.fixture()
def complete_state(complete_relations):
    """Full state with leader and all relations."""
    return testing.State(
        leader=True,
        relations=complete_relations,
    )
