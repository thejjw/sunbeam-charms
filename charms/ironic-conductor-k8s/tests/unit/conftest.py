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

"""Shared fixtures for ironic-conductor-k8s unit tests."""

import json
from pathlib import (
    Path,
)
from unittest import (
    mock,
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
)

CHARM_ROOT = Path(__file__).parents[2]


def _ceph_rgw_relation_complete() -> testing.Relation:
    """ceph-rgw-ready relation with ready=true."""
    return testing.Relation(
        endpoint="ceph-rgw-ready",
        remote_app_name="microceph",
        remote_app_data={"ready": "true"},
        remote_units_data={0: {}},
    )


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture(autouse=True)
def _mock_lightkube():
    """Mock lightkube Client used by KubernetesLoadBalancerHandler."""
    with mock.patch("ops_sunbeam.k8s_resource_handlers.Client") as mock_client:
        client = mock_client.return_value
        svc = client.get.return_value
        svc.status.loadBalancer.ingress = [mock.Mock(ip="10.0.0.100")]
        yield mock_client


@pytest.fixture()
def ctx():
    """Create a testing.Context for IronicConductorOperatorCharm."""
    return testing.Context(
        charm.IronicConductorOperatorCharm, charm_root=CHARM_ROOT
    )


def _peer_relation_with_leader_data() -> testing.PeerRelation:
    """Peer relation with leader_ready and temp_url_secret set in app data."""
    return testing.PeerRelation(
        endpoint="peers",
        local_app_data={
            "leader_ready": json.dumps(True),
            "temp_url_secret": "fake-temp-url-secret",
        },
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        amqp_relation_complete(),
        identity_credentials_relation_complete(),
        _peer_relation_with_leader_data(),
        _ceph_rgw_relation_complete(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret(), identity_credentials_secret()]


@pytest.fixture()
def container():
    """A connectable ironic-conductor container with exec mocks."""
    execs = [
        testing.Exec(command_prefix=["a2dissite"], return_code=0),
        testing.Exec(command_prefix=["a2ensite"], return_code=0),
        testing.Exec(command_prefix=["sudo"], return_code=0),
    ]
    return k8s_container("ironic-conductor", can_connect=True, execs=execs)


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container):
    """Full state with leader, all relations, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
    )
