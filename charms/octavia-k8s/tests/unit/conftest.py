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

"""Shared fixtures for octavia-k8s unit tests."""

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
    identity_service_relation_complete,
    identity_service_secret,
    ingress_internal_relation_complete,
    ingress_public_relation_complete,
    k8s_api_container,
    k8s_container,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]


# ---------------------------------------------------------------------------
# ovsdb-cms relation factory (local, not in shared helpers)
# ---------------------------------------------------------------------------


def ovsdb_cms_relation_complete(
    endpoint: str = "ovsdb-cms",
) -> testing.Relation:
    """ovsdb-cms relation with bound-address set on the remote unit."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ovn-central",
        remote_app_data={},
        remote_units_data={0: {"bound-address": "10.0.0.50"}},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_lightkube_client():
    """Fixture providing a mock lightkube client (no real K8s calls)."""
    return mock.MagicMock()


@pytest.fixture(autouse=True)
def _patch_lightkube(mock_lightkube_client):
    """Patch KubernetesResourcePatcher.lightkube_client in every test."""
    with mock.patch.object(
        charm.KubernetesResourcePatcher,
        "lightkube_client",
        new_callable=mock.PropertyMock,
        return_value=mock_lightkube_client,
    ):
        yield


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for OctaviaOperatorCharm."""
    return testing.Context(charm.OctaviaOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        amqp_relation_complete(),
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        ovsdb_cms_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret(), identity_service_secret()]


@pytest.fixture()
def api_container():
    """A connectable octavia-api container with standard exec mocks."""
    return k8s_api_container(
        "octavia-api",
        extra_execs=[
            testing.Exec(command_prefix=["chown"], return_code=0),
            testing.Exec(command_prefix=["octavia-db-manage"], return_code=0),
        ],
    )


@pytest.fixture()
def driver_agent_container():
    """A connectable octavia-driver-agent container."""
    return k8s_container(
        "octavia-driver-agent",
        execs=[testing.Exec(command_prefix=["chown"], return_code=0)],
    )


@pytest.fixture()
def housekeeping_container():
    """A connectable octavia-housekeeping container."""
    return k8s_container("octavia-housekeeping")


@pytest.fixture()
def health_manager_container():
    """A connectable octavia-health-manager container."""
    return k8s_container("octavia-health-manager")


@pytest.fixture()
def worker_container():
    """A connectable octavia-worker container."""
    return k8s_container("octavia-worker")


@pytest.fixture()
def all_containers(
    api_container,
    driver_agent_container,
    housekeeping_container,
    health_manager_container,
    worker_container,
):
    """All five containers in connectable state."""
    return [
        api_container,
        driver_agent_container,
        housekeeping_container,
        health_manager_container,
        worker_container,
    ]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, all_containers):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=all_containers,
        secrets=complete_secrets,
    )


# ---------------------------------------------------------------------------
# Amphora-specific fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def amphora_config():
    """Config dict enabling Amphora with all required certificate settings."""
    return {
        "amphora-network-attachment": "octavia-mgmt",
        # base64("test") = dGVzdA==
        "lb-mgmt-issuing-cacert": "dGVzdA==",
        "lb-mgmt-issuing-ca-private-key": "dGVzdA==",
        "lb-mgmt-issuing-ca-key-passphrase": "s3cr3t-passphrase",
        "lb-mgmt-controller-cacert": "dGVzdA==",
        "lb-mgmt-controller-cert": "dGVzdA==",
    }


@pytest.fixture()
def barbican_ready_relation():
    """A barbican-service relation whose remote app reports itself ready."""
    return testing.Relation(
        endpoint="barbican-service",
        remote_app_name="barbican-k8s",
        remote_app_data={"ready": "true"},
    )


@pytest.fixture()
def complete_relations_with_barbican(
    complete_relations, barbican_ready_relation
):
    """All mandatory relations plus a ready barbican-service relation."""
    return complete_relations + [barbican_ready_relation]
