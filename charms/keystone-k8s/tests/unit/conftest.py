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

"""Shared fixtures for keystone-k8s unit tests."""

from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    cleanup_database_requires_events,
    db_credentials_secret,
    db_relation_complete,
    ingress_internal_relation_complete,
    ingress_public_relation_complete,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]


def _keystone_manager_mock():
    """Create a mock KeystoneManager that satisfies bootstrap checks."""

    def _obj(name, obj_id):
        return {"id": obj_id, "name": name}

    service_domain = _obj("service_domain", "sdomain_id")
    admin_domain = _obj("admin_domain", "adomain_id")

    def _show_domain(name):
        return admin_domain if name == "admin_domain" else service_domain

    km = MagicMock()
    km.ksclient.show_domain.side_effect = _show_domain
    km.ksclient.show_project.return_value = _obj("admin", "aproject_id")
    km.ksclient.show_user.return_value = _obj("admin", "auser_id")
    km.ksclient.create_user.return_value = _obj("svcuser", "svcuser_id")
    km.ksclient.create_role.return_value = _obj("admin", "arole_id")
    km.create_service_account.return_value = _obj("svcuser", "svcuser_id")
    km.read_keys.return_value = {
        "0": "key0data=",
        "1": "key1data=",
    }
    return km


@pytest.fixture(autouse=True)
def _cleanup_db_events():
    """Remove dynamically-defined events so the next Context can re-create them."""
    yield
    cleanup_database_requires_events()


@pytest.fixture(autouse=True)
def _mock_keystone_internals(monkeypatch):
    """Mock KeystoneManager and pwgen so the charm can bootstrap."""
    km = _keystone_manager_mock()
    monkeypatch.setattr(
        charm.manager, "KeystoneManager", MagicMock(return_value=km)
    )
    monkeypatch.setattr(charm.pwgen, "pwgen", lambda n: "randompassword")
    # Prevent real HTTP requests from _get_oidc_metadata (used by oauth/external-idp).
    monkeypatch.setattr(
        charm._BaseIDPHandler,
        "_get_oidc_metadata",
        lambda self, url, additional_chain=[]: {"mocked": True},
    )


@pytest.fixture()
def ctx():
    """Create a testing.Context for KeystoneOperatorCharm."""
    return testing.Context(charm.KeystoneOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def complete_relations():
    """All relations needed to reach active status."""
    return [
        db_relation_complete(),
        ingress_internal_relation_complete(),
        ingress_public_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return [db_credentials_secret()]


@pytest.fixture()
def storages():
    """Storages required by keystone."""
    return [
        testing.Storage("fernet-keys"),
        testing.Storage("credential-keys"),
    ]


@pytest.fixture()
def container(tmp_path):
    """A connectable keystone container with standard exec mocks and filesystem."""
    # Pre-create directories the charm expects to list_files on.
    saml_dir = tmp_path / "saml-providers"
    saml_dir.mkdir()
    oidc_dir = tmp_path / "oidc-metadata"
    oidc_dir.mkdir()
    domain_dir = tmp_path / "domains"
    domain_dir.mkdir()
    domain_ca_dir = tmp_path / "domain-ca"
    domain_ca_dir.mkdir()

    execs = [
        testing.Exec(command_prefix=["a2dissite"], return_code=0),
        testing.Exec(command_prefix=["a2ensite"], return_code=0),
        testing.Exec(command_prefix=["sudo"], return_code=0),
        testing.Exec(command_prefix=["keystone-manage"], return_code=0),
    ]
    return testing.Container(
        name="keystone",
        can_connect=True,
        mounts={
            "saml-providers": testing.Mount(
                location="/etc/apache2/saml2-metadata/providers",
                source=saml_dir,
            ),
            "oidc-metadata": testing.Mount(
                location="/etc/apache2/oidc-metadata",
                source=oidc_dir,
            ),
            "domains": testing.Mount(
                location="/etc/keystone/domains",
                source=domain_dir,
            ),
            "domain-ca": testing.Mount(
                location="/usr/local/share/ca-certificates",
                source=domain_ca_dir,
            ),
        },
        execs=frozenset(execs),
    )


@pytest.fixture()
def complete_state(complete_relations, complete_secrets, container, storages):
    """Full state with leader, all relations, secrets, container, and storages."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        containers=[container],
        secrets=complete_secrets,
        storages=storages,
    )
