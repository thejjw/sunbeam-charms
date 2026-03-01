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

"""Shared fixtures for nova-ironic-k8s unit tests."""

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
    identity_credentials_relation_complete,
    identity_credentials_secret,
    k8s_container,
    peer_relation,
    traefik_route_relation_complete,
)

CHARM_ROOT = Path(__file__).parents[2]


def ironic_api_relation_complete(
    endpoint: str = "ironic-api",
) -> testing.Relation:
    """Ironic-api relation with ready flag set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ironic",
        remote_app_data={"ready": "true"},
        remote_units_data={0: {}},
    )


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for NovaIronicOperatorCharm."""
    return testing.Context(
        charm.NovaIronicOperatorCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        amqp_relation_complete(),
        identity_credentials_relation_complete(),
        ironic_api_relation_complete(),
        traefik_route_relation_complete(endpoint="traefik-route-internal"),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret(), identity_credentials_secret()]


@pytest.fixture()
def container():
    """A connectable nova-ironic container."""
    return k8s_container("nova-ironic")


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container):
    """Full state with leader, all relations, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
    )
