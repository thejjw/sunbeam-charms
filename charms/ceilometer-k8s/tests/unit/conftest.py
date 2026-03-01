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

"""Shared fixtures for ceilometer-k8s unit tests."""

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
    identity_credentials_relation_complete,
    identity_credentials_secret,
    k8s_container,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]


def gnocchi_db_relation_complete(
    endpoint: str = "gnocchi-db",
) -> testing.Relation:
    """Gnocchi-db relation with ready flag set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="gnocchi",
        remote_app_data={"ready": "true"},
        remote_units_data={0: {}},
    )


def ceilometer_container(
    name: str,
    can_connect: bool = True,
) -> testing.Container:
    """Create a ceilometer container with the ceilometer-upgrade exec mock."""
    execs = [
        testing.Exec(command_prefix=["ceilometer-upgrade"], return_code=0),
    ]
    return k8s_container(name, can_connect=can_connect, execs=execs)


@pytest.fixture(autouse=True)
def _cleanup_gnocchi_events():
    """Remove dynamically-defined events for GnocchiServiceRequires."""
    yield
    from charms.gnocchi_k8s.v0.gnocchi_service import (
        GnocchiServiceRequirerEvents,
    )

    for attr in list(vars(GnocchiServiceRequirerEvents)):
        if attr.endswith(("_readiness_changed", "_goneaway")):
            try:
                delattr(GnocchiServiceRequirerEvents, attr)
            except AttributeError:
                pass


@pytest.fixture()
def ctx():
    """Create a testing.Context for CeilometerOperatorCharm."""
    return testing.Context(
        charm.CeilometerOperatorCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        amqp_relation_complete(),
        identity_credentials_relation_complete(),
        gnocchi_db_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [identity_credentials_secret()]


@pytest.fixture()
def containers():
    """Both ceilometer containers, connectable."""
    return [
        ceilometer_container("ceilometer-central"),
        ceilometer_container("ceilometer-notification"),
    ]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, containers):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=containers,
        secrets=complete_secrets,
    )
