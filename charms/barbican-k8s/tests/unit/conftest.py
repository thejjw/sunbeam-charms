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

"""Shared fixtures for barbican-k8s unit tests."""

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
    k8s_api_container,
    k8s_container,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]

VAULT_NONCE = "testnonce0123456789abcdef"
VAULT_CREDS_SECRET_ID = "secret:vault-creds"


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


# ---------------------------------------------------------------------------
# Vault-KV helpers (local to barbican-k8s)
# ---------------------------------------------------------------------------


def vault_kv_nonce_secret() -> testing.Secret:
    """Unit-owned nonce secret created at install time."""
    return testing.Secret(
        tracked_content={"nonce": VAULT_NONCE},
        owner="unit",
        label="nonce",
    )


def vault_kv_credentials_secret(
    secret_id: str = VAULT_CREDS_SECRET_ID,
) -> testing.Secret:
    """Secret containing vault approle credentials (owned by vault provider)."""
    return testing.Secret(
        tracked_content={
            "role-id": "test-role-id",
            "role-secret-id": "test-role-secret-id",
        },
        id=secret_id,
        owner=None,
    )


def vault_kv_relation_complete(
    creds_secret_id: str = VAULT_CREDS_SECRET_ID,
) -> testing.Relation:
    """Vault-KV relation with all provider data set."""
    return testing.Relation(
        endpoint="vault-kv",
        remote_app_name="vault",
        remote_app_data={
            "vault_url": "https://vault.example.com:8200",
            "mount": "charm-barbican-secrets",
            "ca_certificate": "-----BEGIN CERTIFICATE-----\nfakecert\n-----END CERTIFICATE-----",
            "credentials": json.dumps({VAULT_NONCE: creds_secret_id}),
        },
        remote_units_data={0: {}},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ctx():
    """Create a testing.Context for BarbicanVaultOperatorCharm."""
    return testing.Context(
        charm.BarbicanVaultOperatorCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        amqp_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        vault_kv_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [
        db_credentials_secret(),
        identity_service_secret(),
        vault_kv_nonce_secret(),
        vault_kv_credentials_secret(),
    ]


@pytest.fixture()
def api_container():
    """Connectable barbican-api container with standard exec mocks."""
    return k8s_api_container(
        "barbican-api",
        extra_execs=[
            testing.Exec(command_prefix=["a2disconf"], return_code=0),
        ],
    )


@pytest.fixture()
def worker_container():
    """Connectable barbican-worker container."""
    return k8s_container("barbican-worker")


@pytest.fixture()
def containers(api_container, worker_container):
    """Both containers."""
    return [api_container, worker_container]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, containers):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=containers,
        secrets=complete_secrets,
    )
