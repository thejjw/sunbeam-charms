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

"""Shared fixtures for sunbeam-ovn-proxy unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture()
def ctx():
    """Create a testing.Context for SunbeamOvnProxyCharm."""
    return testing.Context(charm.SunbeamOvnProxyCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def ovsdb_relation_complete():
    """An ovsdb relation with connection string data (ready)."""
    return testing.Relation(
        endpoint="ovsdb",
        interface="ovsdb",
        remote_app_data={
            "db_nb_connection_str": "tcp:127.0.0.1:6641",
            "db_sb_connection_str": "tcp:127.0.0.1:6641",
        },
    )


@pytest.fixture()
def ovsdb_relation_empty():
    """An ovsdb relation with no data (not ready)."""
    return testing.Relation(
        endpoint="ovsdb",
        interface="ovsdb",
    )


@pytest.fixture()
def ovsdb_cms_relation():
    """An ovsdb-cms provides relation."""
    return testing.Relation(
        endpoint="ovsdb-cms",
        interface="ovsdb-cms",
    )


@pytest.fixture()
def complete_relations(ovsdb_relation_complete):
    """All relations needed to reach active status."""
    return [ovsdb_relation_complete]


@pytest.fixture()
def complete_state(complete_relations):
    """Full state with leader and all required relations."""
    return testing.State(
        leader=True,
        relations=complete_relations,
    )
