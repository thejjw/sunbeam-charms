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

"""Shared fixtures for tempest-k8s unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    k8s_container,
)

CHARM_ROOT = Path(__file__).parents[2]

# The secret label used by TempestUserIdentityRelationHandler to store
# credentials in peer app data.
IDENTITY_OPS_SECRET_LABEL = (
    "tempest-user-identity-resource-CloudValidation-test-user"
)
IDENTITY_OPS_SECRET_ID = "secret:tempest-creds"


# ---------------------------------------------------------------------------
# Local relation / secret factories (identity-ops is charm-specific)
# ---------------------------------------------------------------------------


def identity_ops_secret(
    secret_id: str = IDENTITY_OPS_SECRET_ID,
) -> testing.Secret:
    """Secret containing identity-ops credentials (owned by the app)."""
    return testing.Secret(
        tracked_content={
            "username": "tempest",
            "password": "password",
            "project-name": "CloudValidation-tempest",
            "domain-name": "tempest",
            "domain-id": "tempest-domain-id",
            "auth-url": "http://10.6.0.23/openstack-keystone/v3",
        },
        id=secret_id,
        label=IDENTITY_OPS_SECRET_LABEL,
        owner="app",
    )


def identity_ops_relation() -> testing.Relation:
    """identity-ops relation (keystone-resources interface)."""
    return testing.Relation(
        endpoint="identity-ops",
        remote_app_name="keystone",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def peer_relation_with_credential(
    secret_id: str = IDENTITY_OPS_SECRET_ID,
) -> testing.PeerRelation:
    """Peer relation with the identity-ops secret reference in app data."""
    return testing.PeerRelation(
        endpoint="peers",
        local_app_data={
            IDENTITY_OPS_SECRET_LABEL: secret_id,
        },
    )


def tempest_container(
    can_connect: bool = True,
) -> testing.Container:
    """Tempest container with exec mocks for cleanup and init."""
    execs = [
        testing.Exec(command_prefix=["python3"], return_code=0),
        testing.Exec(command_prefix=["tempest-init"], return_code=0),
    ]
    return k8s_container(
        "tempest",
        can_connect=can_connect,
        execs=execs,
    )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ctx():
    """Create a testing.Context for TempestOperatorCharm."""
    return testing.Context(charm.TempestOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        identity_ops_relation(),
        peer_relation_with_credential(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [identity_ops_secret()]


@pytest.fixture()
def container():
    """A connectable tempest container with exec mocks."""
    return tempest_container()


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container):
    """Full state with leader, all relations, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
    )
