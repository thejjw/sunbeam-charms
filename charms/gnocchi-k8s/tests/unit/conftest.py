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

"""Shared fixtures for gnocchi-k8s unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    ceph_relation_complete,
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
    sudo_exec,
)

CHARM_ROOT = Path(__file__).parents[2]


def s3_credentials_relation_complete(
    endpoint: str = "s3-credentials",
) -> testing.Relation:
    """S3 credentials relation with access-key, secret-key, and endpoint set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="s3-integrator",
        remote_app_data={
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "endpoint": "http://s3.example.com:9000",
            "bucket": "gnocchi",
            "region": "us-east-1",
        },
        remote_units_data={},
    )


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for GnocchiCephOperatorCharm."""
    return testing.Context(
        charm.GnocchiCephOperatorCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        ceph_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret(), identity_service_secret()]


CEPH_EXECS = [
    testing.Exec(command_prefix=["ceph-authtool"], return_code=0),
    testing.Exec(command_prefix=["chown"], return_code=0),
    testing.Exec(command_prefix=["chmod"], return_code=0),
    testing.Exec(command_prefix=["gnocchi-upgrade"], return_code=0),
]


@pytest.fixture()
def gnocchi_api_container():
    """A connectable gnocchi-api container with standard exec mocks."""
    return k8s_api_container("gnocchi-api", extra_execs=CEPH_EXECS)


@pytest.fixture()
def gnocchi_metricd_container():
    """A connectable gnocchi-metricd container with exec mocks."""
    return k8s_container(
        "gnocchi-metricd",
        execs=[sudo_exec()] + CEPH_EXECS,
    )


@pytest.fixture()
def containers(gnocchi_api_container, gnocchi_metricd_container):
    """Both containers for the charm."""
    return [gnocchi_api_container, gnocchi_metricd_container]


@pytest.fixture()
def ceph_stored_state():
    """Pre-seed CephClientRequires stored state so pools_available is True."""
    return testing.StoredState(
        owner_path="GnocchiCephOperatorCharm/CephClientRequires[ceph]",
        name="_stored",
        content={
            "pools_available": True,
            "broker_available": True,
            "broker_req": {},
        },
    )


@pytest.fixture()
def complete_state(
    complete_relations, complete_secrets, containers, ceph_stored_state
):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=containers,
        secrets=complete_secrets,
        stored_states=[ceph_stored_state],
    )


S3_EXECS = [
    testing.Exec(command_prefix=["gnocchi-upgrade"], return_code=0),
]


@pytest.fixture()
def gnocchi_api_container_s3():
    """A connectable gnocchi-api container with S3 exec mocks (no ceph tools)."""
    return k8s_api_container("gnocchi-api", extra_execs=S3_EXECS)


@pytest.fixture()
def gnocchi_metricd_container_s3():
    """A connectable gnocchi-metricd container with S3 exec mocks."""
    return k8s_container(
        "gnocchi-metricd",
        execs=[sudo_exec()] + S3_EXECS,
    )


@pytest.fixture()
def containers_s3(gnocchi_api_container_s3, gnocchi_metricd_container_s3):
    """Both containers configured for S3 (no ceph exec mocks)."""
    return [gnocchi_api_container_s3, gnocchi_metricd_container_s3]


@pytest.fixture()
def complete_relations_s3():
    """All relations needed to reach active status using S3 backend."""
    return [
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        s3_credentials_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_state_s3(complete_relations_s3, complete_secrets, containers_s3):
    """Full state with leader, S3 relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations_s3,
        containers=containers_s3,
        secrets=complete_secrets,
    )


@pytest.fixture()
def state_no_storage_backend(complete_secrets, containers_s3):
    """State with all mandatory relations except any storage backend (no ceph, no s3)."""
    return testing.State(
        leader=True,
        containers=containers_s3,
        relations=[
            db_relation_complete(),
            identity_service_relation_complete(),
            ingress_internal_relation_complete(),
            ingress_public_relation_complete(),
            peer_relation(),
        ],
        secrets=complete_secrets,
    )
