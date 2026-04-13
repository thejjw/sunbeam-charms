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
from ops_sunbeam import relation_handlers as sunbeam_rhandlers
from ops_sunbeam.test_utils_scenario import (
    amqp_relation_complete,
    certificates_relation_complete,
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
        certificates_relation_complete(),
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
            testing.Exec(
                command_prefix=["update-ca-certificates"], return_code=0
            ),
        ],
    )


@pytest.fixture()
def controller_container():
    """A connectable octavia-controller container."""
    return k8s_container(
        "octavia-controller",
        execs=[
            testing.Exec(command_prefix=["chown"], return_code=0),
            testing.Exec(
                command_prefix=["update-ca-certificates"], return_code=0
            ),
        ],
    )


@pytest.fixture()
def all_containers(api_container, controller_container):
    """All containers in connectable state."""
    return [api_container, controller_container]


@pytest.fixture(autouse=True)
def mock_cni_ready():
    """Mock CNI infrastructure as ready in every test (autouse).

    cni_ready() calls the real Kubernetes API which is not available in unit
    tests.  This fixture makes it return (True, "") by default so tests that
    exercise the Amphora path are not blocked by missing CNI DaemonSets.
    Tests that specifically verify the CNI-not-ready waiting state should
    override this fixture locally with a return value of (False, "reason").
    """
    with mock.patch.object(
        charm.OctaviaNetworkAnnotationPatcher,
        "cni_ready",
        return_value=(True, ""),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_certs_ready():
    """Mock the OVN TlsCertificatesHandler as ready (autouse).

    certificates_relation_complete() provides data that fails the v4
    JSON schema validation, so the handler never reaches ready=True in
    a state-transition test.  Mocking it here lets the charm proceed past
    the 'certificates incomplete' guard without needing a real cert bundle.
    Tests that specifically verify the "certificates missing" blocked/waiting
    state still work because they omit the certificates relation from state,
    causing check_relation_handlers_ready() to raise before ready() is called.
    """
    with mock.patch.object(
        sunbeam_rhandlers.TlsCertificatesHandler,
        "ready",
        new_callable=mock.PropertyMock,
        return_value=True,
    ):
        yield


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
def amphora_issuing_ca_relation():
    """An amphora-issuing-ca relation (cert not yet issued)."""
    return testing.Relation(
        endpoint="amphora-issuing-ca",
        remote_app_name="self-signed-certificates",
    )


@pytest.fixture()
def amphora_controller_cert_relation():
    """An amphora-controller-cert relation (cert not yet issued)."""
    return testing.Relation(
        endpoint="amphora-controller-cert",
        remote_app_name="self-signed-certificates",
    )


@pytest.fixture()
def mock_amphora_certs_ready():
    """Mock Amphora TLS cert handlers as ready to skip cert-readiness waiting."""
    with mock.patch.object(
        charm.AmphoraTlsCertificatesHandler,
        "ready",
        new_callable=mock.PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture()
def mock_amphora_cert_context():
    """Mock AmphoraCertificatesContext.context with fake cert data."""
    with mock.patch.object(
        charm.AmphoraCertificatesContext,
        "context",
        return_value={
            "lb_mgmt_issuing_cacert": "FAKE_CA",
            "lb_mgmt_issuing_ca_private_key": "FAKE_KEY",
            "lb_mgmt_issuing_ca_root": "FAKE_ROOT_CA",
            "lb_mgmt_controller_cacert": "FAKE_CA",
            "lb_mgmt_controller_cert": "FAKE_CERT",
        },
    ):
        yield


@pytest.fixture()
def amphora_config():
    """Config dict enabling Amphora (cert relations replace old config options)."""
    return {"amphora-network-attachment": "octavia-mgmt"}


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
    complete_relations,
    barbican_ready_relation,
    amphora_issuing_ca_relation,
    amphora_controller_cert_relation,
):
    """All mandatory relations plus barbican and Amphora TLS cert relations."""
    return complete_relations + [
        barbican_ready_relation,
        amphora_issuing_ca_relation,
        amphora_controller_cert_relation,
    ]
