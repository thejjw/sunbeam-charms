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

"""Shared fixtures for openstack-exporter-k8s unit tests."""

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
    k8s_container,
)

CHARM_ROOT = Path(__file__).parents[2]


# =========================================================================
# Identity-ops helpers (local – no shared factory exists yet)
# =========================================================================


def identity_ops_config_secret(
    secret_id: str = "secret:id-ops-config-creds",
) -> testing.Secret:
    """Secret containing configure-credential for openstack-exporter."""
    return testing.Secret(
        tracked_content={
            "username": "openstack-exporter-abc123",
            "password": "secretpass",
        },
        id=secret_id,
        label="configure-credential-openstack-exporter",
        owner="app",
    )


def identity_ops_user_secret(
    secret_id: str = "secret:id-ops-user-creds",
) -> testing.Secret:
    """Secret containing user-identity-resource for openstack-exporter."""
    return testing.Secret(
        tracked_content={
            "username": "openstack-exporter-abc123",
            "password": "secretpass",
        },
        id=secret_id,
        label="user-identity-resource-openstack-exporter",
        owner="app",
    )


def auth_url_secret(
    secret_id: str = "secret:auth-url",
) -> testing.Secret:
    """Secret containing the auth-url for the exporter."""
    return testing.Secret(
        tracked_content={
            "auth-url": "http://keystone.internal:5000/v3",
        },
        id=secret_id,
        label="configure-auth-url",
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
                    "tag": "create_user_openstack-exporter",
                    "ops": [
                        {"name": "create_domain", "return-code": 0},
                        {"name": "create_role", "return-code": 0},
                        {"name": "create_user", "return-code": 0},
                        {"name": "grant_role", "return-code": 0},
                        {
                            "name": "list_endpoint",
                            "return-code": 0,
                            "value": [
                                {
                                    "url": "http://keystone.internal:5000/v3",
                                }
                            ],
                        },
                    ],
                }
            )
        },
        remote_units_data={0: {}},
    )


def exporter_peer_relation() -> testing.PeerRelation:
    """Peer relation pre-populated with secret IDs needed by the charm."""
    return testing.PeerRelation(
        endpoint="peers",
        local_app_data={
            "configure-credential-openstack-exporter": "secret:id-ops-config-creds",
            "user-identity-resource-openstack-exporter": "secret:id-ops-user-creds",
            "configure-auth-url": "secret:auth-url",
        },
    )


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture()
def ctx():
    """Create a testing.Context for OSExporterOperatorCharm."""
    return testing.Context(
        charm.OSExporterOperatorCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        identity_ops_relation_complete(),
        exporter_peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [
        identity_ops_config_secret(),
        identity_ops_user_secret(),
        auth_url_secret(),
    ]


@pytest.fixture()
def container():
    """A connectable openstack-exporter container."""
    return k8s_container("openstack-exporter")


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container):
    """Full state with leader, all relations, secrets, and container."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
    )
