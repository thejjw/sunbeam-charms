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

"""Shared fixtures for heat-k8s unit tests."""

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
    k8s_container,
    traefik_route_relation_complete,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for HeatOperatorCharm."""
    return testing.Context(charm.HeatOperatorCharm, charm_root=CHARM_ROOT)


# =========================================================================
# Identity-ops helpers (local – no shared factory exists yet)
# =========================================================================


def identity_ops_config_secret(
    secret_id: str = "secret:id-ops-config-creds",
) -> testing.Secret:
    """Secret containing configure-credential for heat_domain_admin."""
    return testing.Secret(
        tracked_content={
            "username": "heat_domain_admin-abc123",
            "password": "secretpass",
        },
        id=secret_id,
        label="configure-credential-heat_domain_admin",
        owner="app",
    )


def identity_ops_user_secret(
    secret_id: str = "secret:id-ops-user-creds",
) -> testing.Secret:
    """Secret containing user-identity-resource for heat_domain_admin."""
    return testing.Secret(
        tracked_content={
            "username": "heat_domain_admin-abc123",
            "password": "secretpass",
        },
        id=secret_id,
        label="user-identity-resource-heat_domain_admin",
        owner="app",
    )


def auth_encryption_key_secret(
    secret_id: str = "secret:auth-enc-key",
) -> testing.Secret:
    """Secret containing the heat auth encryption key."""
    return testing.Secret(
        tracked_content={
            "auth-encryption-key": "0123456789abcdef0123456789abcdef",
        },
        id=secret_id,
        label="auth-encryption-key",
        owner="app",
    )


def identity_ops_relation_complete(
    endpoint: str = "identity-ops",
) -> testing.Relation:
    """Identity-ops relation with a successful response from keystone."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={
            "response": json.dumps(
                {
                    "id": 1,
                    "tag": "create_user_heat_domain_admin",
                    "ops": [
                        {"name": "create_domain", "return-code": 0},
                        {"name": "create_role", "return-code": 0},
                        {"name": "create_user", "return-code": 0},
                        {"name": "grant_role", "return-code": 0},
                        {"name": "create_role", "return-code": 0},
                    ],
                }
            )
        },
        remote_units_data={0: {}},
    )


def heat_peer_relation() -> testing.PeerRelation:
    """Peer relation pre-populated with secret IDs needed by the charm."""
    return testing.PeerRelation(
        endpoint="peers",
        local_app_data={
            "configure-credential-heat_domain_admin": "secret:id-ops-config-creds",
            "auth-encryption-key": "secret:auth-enc-key",
            "user-identity-resource-heat_domain_admin": "secret:id-ops-user-creds",
        },
        local_unit_data={"host": "heat-0.heat.testing.svc"},
    )


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        amqp_relation_complete(),
        identity_service_relation_complete(),
        identity_ops_relation_complete(),
        traefik_route_relation_complete(endpoint="traefik-route-internal"),
        heat_peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [
        db_credentials_secret(),
        identity_service_secret(),
        identity_ops_config_secret(),
        identity_ops_user_secret(),
        auth_encryption_key_secret(),
    ]


@pytest.fixture()
def heat_api_container():
    """heat-api container with db-sync exec mock."""
    return k8s_container(
        "heat-api",
        execs=[
            testing.Exec(command_prefix=["a2dissite"], return_code=0),
            testing.Exec(command_prefix=["a2ensite"], return_code=0),
            testing.Exec(command_prefix=["heat-manage"], return_code=0),
        ],
    )


@pytest.fixture()
def heat_api_cfn_container():
    """heat-api-cfn container."""
    return k8s_container(
        "heat-api-cfn",
        execs=[
            testing.Exec(command_prefix=["a2dissite"], return_code=0),
            testing.Exec(command_prefix=["a2ensite"], return_code=0),
        ],
    )


@pytest.fixture()
def heat_engine_container():
    """heat-engine container."""
    return k8s_container("heat-engine")


@pytest.fixture()
def containers(
    heat_api_container, heat_api_cfn_container, heat_engine_container
):
    """All three heat containers."""
    return [heat_api_container, heat_api_cfn_container, heat_engine_container]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, containers):
    """Full state with leader, all relations, secrets, and containers."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=containers,
        secrets=complete_secrets,
    )
