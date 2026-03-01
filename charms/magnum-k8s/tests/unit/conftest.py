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

"""Shared fixtures for magnum-k8s unit tests."""

import json
from pathlib import (
    Path,
)

import charm
import pytest
import yaml
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
)

CHARM_ROOT = Path(__file__).parents[2]

# -- identity-ops local factories --

IDOPS_CONFIG_SECRET_ID = "secret:idopsconfigcreds0000"
KUBECONFIG_SECRET_ID = "secret:kubeconfig0000000000"


def identity_ops_config_secret(
    secret_id: str = IDOPS_CONFIG_SECRET_ID,
) -> testing.Secret:
    """Secret holding identity-ops configure credentials (owned by this app)."""
    return testing.Secret(
        tracked_content={"username": "test-user", "password": "test-pass"},
        id=secret_id,
        owner="app",
        label="configure-credential-magnum_domain_admin",
    )


def identity_ops_relation_complete(
    endpoint: str = "identity-ops",
) -> testing.Relation:
    """identity-ops relation with a successful response from keystone."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={
            "response": json.dumps(
                {
                    "id": 1,
                    "tag": "create_user_magnum_domain_admin",
                    "ops": [
                        {"name": "create_domain", "return-code": 0},
                        {"name": "create_role", "return-code": 0},
                        {"name": "create_user", "return-code": 0},
                        {"name": "grant_role", "return-code": 0},
                    ],
                }
            )
        },
        remote_units_data={0: {"ingress-address": "10.0.0.40"}},
    )


def kubeconfig_secret(
    secret_id: str = KUBECONFIG_SECRET_ID,
) -> testing.Secret:
    """Secret holding kubeconfig content."""
    return testing.Secret(
        tracked_content={"kubeconfig": yaml.dump({"cluster": "testcluster"})},
        id=secret_id,
        owner="app",
    )


def magnum_peer_relation(
    config_secret_id: str = IDOPS_CONFIG_SECRET_ID,
) -> testing.PeerRelation:
    """Peer relation pre-populated with identity-ops config credentials ref."""
    return testing.PeerRelation(
        endpoint="peers",
        local_app_data={
            "configure-credential-magnum_domain_admin": config_secret_id,
        },
    )


# -- pytest fixtures --


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture()
def ctx():
    """Create a testing.Context for MagnumOperatorCharm."""
    return testing.Context(charm.MagnumOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        identity_service_relation_complete(),
        ingress_internal_relation_complete(),
        amqp_relation_complete(),
        identity_ops_relation_complete(),
        magnum_peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [
        db_credentials_secret(),
        identity_service_secret(),
        identity_ops_config_secret(),
        kubeconfig_secret(),
    ]


@pytest.fixture()
def api_container():
    """A connectable magnum-api container with standard exec mocks."""
    return k8s_api_container("magnum-api")


@pytest.fixture()
def conductor_container():
    """A connectable magnum-conductor container."""
    return k8s_container("magnum-conductor")


@pytest.fixture()
def containers(api_container, conductor_container):
    """Both magnum containers."""
    return [api_container, conductor_container]


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, containers):
    """Full state with leader, all relations, secrets, containers, and config."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=containers,
        secrets=complete_secrets,
        config={"kubeconfig": KUBECONFIG_SECRET_ID},
    )
