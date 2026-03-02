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

"""Shared fixtures for manila-data unit tests (ops.testing)."""

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
from ops_sunbeam.test_utils_scenario import (
    amqp_relation_complete,
    cleanup_database_requires_events,
    db_credentials_secret,
    db_relation_complete,
    identity_credentials_relation_complete,
    identity_credentials_secret,
)

CHARM_ROOT = Path(__file__).parents[2]

# ---- Mandatory relations (non-optional requires from charmcraft.yaml) ----
# amqp, database, identity-credentials


def _all_mandatory_relations() -> list:
    return [
        amqp_relation_complete(),
        db_relation_complete(),
        identity_credentials_relation_complete(),
    ]


def _all_secrets() -> list:
    return [
        db_credentials_secret(),
        identity_credentials_secret(),
    ]


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    yield
    cleanup_database_requires_events()


@pytest.fixture(autouse=True)
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment."""
    # snap module
    mock_snap = MagicMock()
    mock_snap.SnapError = Exception
    mock_snap.SnapNotFoundError = Exception
    mock_snap.SnapState.Latest = "latest"
    manila_data_snap = MagicMock()
    manila_data_snap.present = False
    mock_snap.SnapCache.return_value = {
        "manila-data": manila_data_snap,
    }
    mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(
        charm.ManilaDataOperatorCharm, "_import_snap", lambda self: mock_snap
    )


@pytest.fixture()
def ctx():
    """Create a testing.Context for ManilaDataOperatorCharm."""
    return testing.Context(
        charm.ManilaDataOperatorCharm,
        charm_root=CHARM_ROOT,
    )


@pytest.fixture()
def complete_relations():
    """All mandatory relations needed to reach active status."""
    return _all_mandatory_relations()


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return _all_secrets()


@pytest.fixture()
def complete_state(complete_relations, complete_secrets):
    """Full state with leader and all mandatory relations + secrets."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        secrets=complete_secrets,
    )
