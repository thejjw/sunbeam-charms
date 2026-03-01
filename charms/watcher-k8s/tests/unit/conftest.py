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

"""Shared fixtures for watcher-k8s unit tests."""

from pathlib import (
    Path,
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
    identity_service_relation_complete,
    identity_service_secret,
    ingress_internal_relation_complete,
    k8s_api_container,
    k8s_container,
    peer_relation,
    sudo_exec,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for WatcherOperatorCharm."""
    return testing.Context(charm.WatcherOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        amqp_relation_complete(),
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret(), identity_service_secret()]


@pytest.fixture()
def api_container():
    """A connectable watcher-api container with standard exec mocks."""
    return k8s_api_container("watcher-api")


@pytest.fixture()
def applier_container():
    """A connectable watcher-applier container with sudo exec mock."""
    return k8s_container("watcher-applier", execs=[sudo_exec()])


@pytest.fixture()
def decision_engine_container():
    """A connectable watcher-decision-engine container with sudo exec mock."""
    return k8s_container("watcher-decision-engine", execs=[sudo_exec()])


@pytest.fixture()
def all_containers(
    api_container, applier_container, decision_engine_container
):
    """All three watcher containers."""
    return [api_container, applier_container, decision_engine_container]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, all_containers):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=all_containers,
        secrets=complete_secrets,
    )
