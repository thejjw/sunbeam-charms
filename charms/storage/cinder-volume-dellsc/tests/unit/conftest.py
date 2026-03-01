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

"""Shared fixtures for cinder-volume-dellsc unit tests (ops.testing)."""

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


def cinder_volume_relation() -> testing.SubordinateRelation:
    """cinder-volume subordinate relation with snap-name data."""
    return testing.SubordinateRelation(
        endpoint="cinder-volume",
        remote_app_name="cinder-volume",
        remote_unit_data={"snap-name": "cinder-volume"},
    )


@pytest.fixture(autouse=True)
def _mock_heavy_externals(monkeypatch):
    """Patch snap module so snap operations are no-ops in tests."""
    mock_snap = MagicMock()
    mock_snap.SnapError = Exception
    mock_snap.SnapNotFoundError = Exception
    mock_snap.SnapState.Latest = "latest"
    cinder_volume_snap = MagicMock()
    cinder_volume_snap.present = False
    mock_snap.SnapCache.return_value = {
        "cinder-volume": cinder_volume_snap,
    }
    mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(
        charm.CinderVolumeDellSCOperatorCharm,
        "_import_snap",
        lambda self: mock_snap,
    )


@pytest.fixture()
def ctx():
    """Create a testing.Context for CinderVolumeDellSCOperatorCharm."""
    return testing.Context(
        charm.CinderVolumeDellSCOperatorCharm,
        charm_root=CHARM_ROOT,
    )


@pytest.fixture()
def complete_state():
    """Full state with leader, cinder-volume relation, config and secrets."""
    san_secret = testing.Secret(
        tracked_content={
            "san-login": "admin",
            "san-password": "secret",
        },
        owner="app",
    )
    return testing.State(
        leader=True,
        config={
            "san-ip": "10.0.0.1",
            "san-login": san_secret.id,
            "san-password": san_secret.id,
            "dell-sc-ssn": 12345,
        },
        relations=[cinder_volume_relation()],
        secrets=[san_secret],
    )
