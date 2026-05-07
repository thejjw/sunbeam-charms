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

"""Shared fixtures for designate-k8s unit tests."""

import json
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
    ingress_public_relation_complete,
    k8s_api_container,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]

NONCE = "test-nonce-hex"
RNDC_SECRET_ID = "secret:rndc-key"


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for DesignateOperatorCharm."""
    return testing.Context(charm.DesignateOperatorCharm, charm_root=CHARM_ROOT)


# ---------------------------------------------------------------------------
# dns-backend (bind-rndc) relation helpers
# ---------------------------------------------------------------------------


def dns_backend_relation_complete(
    nonce: str = NONCE,
    rndc_secret_id: str = RNDC_SECRET_ID,
) -> testing.Relation:
    """dns-backend relation with host and rndc_keys set."""
    return testing.Relation(
        endpoint="dns-backend",
        remote_app_name="bind9",
        remote_app_data={
            "host": "10.20.20.20",
            "rndc_keys": json.dumps(
                {nonce: {"algorithm": "hmac-256", "secret": rndc_secret_id}}
            ),
        },
        local_unit_data={"nonce": nonce},
        remote_units_data={0: {}},
    )


def dns_backend_relation_ha(
    nonce: str = NONCE,
    rndc_secret_id: str = RNDC_SECRET_ID,
) -> testing.Relation:
    """dns-backend relation with 3 bind units publishing per-unit hosts."""
    return testing.Relation(
        endpoint="dns-backend",
        remote_app_name="bind9",
        remote_app_data={
            "host": "10.20.20.20",
            "rndc_keys": json.dumps(
                {nonce: {"algorithm": "hmac-256", "secret": rndc_secret_id}}
            ),
        },
        local_unit_data={"nonce": nonce},
        remote_units_data={
            0: {"host": "bind-0.bind-endpoints.openstack.svc.cluster.local"},
            1: {"host": "bind-1.bind-endpoints.openstack.svc.cluster.local"},
            2: {"host": "bind-2.bind-endpoints.openstack.svc.cluster.local"},
        },
    )


def nonce_secret() -> testing.Secret:
    """Unit secret for bind-rndc nonce."""
    return testing.Secret(
        tracked_content={"nonce": NONCE},
        owner="unit",
        label="nonce-rndc",
    )


def rndc_key_secret(
    secret_id: str = RNDC_SECRET_ID,
) -> testing.Secret:
    """Model secret for rndc key (owned by remote app bind9)."""
    return testing.Secret(
        tracked_content={"secret": "rndc_secret_value"},
        id=secret_id,
        owner=None,
    )


# ---------------------------------------------------------------------------
# Composite fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        amqp_relation_complete(),
        dns_backend_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_relations_ha():
    """All relations with 3-unit bind backend for HA testing."""
    return [
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        amqp_relation_complete(),
        dns_backend_relation_ha(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [
        db_credentials_secret(),
        identity_service_secret(),
        nonce_secret(),
        rndc_key_secret(),
    ]


@pytest.fixture()
def container():
    """A connectable designate container with standard exec mocks."""
    return k8s_api_container("designate")


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container):
    """Full state with leader, all relations, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
    )


@pytest.fixture()
def complete_state_ha(complete_relations_ha, complete_secrets, container):
    """Full state with leader, HA bind backend, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations_ha,
        containers=[container],
        secrets=complete_secrets,
    )
