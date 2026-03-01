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

"""Shared fixtures for openstack-hypervisor unit tests (ops.testing)."""

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
    amqp_relation_complete,
    identity_credentials_relation_complete,
    identity_credentials_secret,
)

CHARM_ROOT = Path(__file__).parents[2]

# ---- relation / secret builders for hypervisor-specific endpoints ----

OVSDB_CMS_APP_DATA = {
    "loadbalancer-address": "10.15.24.37",
    "sb-connection-string": "ssl:10.15.24.37:6642",
}
OVSDB_CMS_UNIT_DATA = {
    "bound-address": "10.1.176.143",
    "bound-hostname": "ovn-relay-0.ovn-relay-endpoints.openstack.svc.cluster.local",
    "egress-subnets": "10.20.21.10/32",
    "ingress-address": "10.20.21.10",
}


def ovsdb_cms_relation() -> testing.Relation:
    """ovsdb-cms relation with loadbalancer address (external_connectivity)."""
    return testing.Relation(
        endpoint="ovsdb-cms",
        remote_app_name="ovn-relay",
        remote_app_data=OVSDB_CMS_APP_DATA,
        remote_units_data={0: OVSDB_CMS_UNIT_DATA},
    )


def nova_service_relation() -> testing.Relation:
    """nova-service relation with spice-proxy-url."""
    return testing.Relation(
        endpoint="nova-service",
        remote_app_name="nova",
        remote_app_data={
            "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.50"}},
    )


# ---- Mandatory relations (non-optional requires from charmcraft.yaml) ----
# amqp, identity-credentials, ovsdb-cms, nova-service


def _all_mandatory_relations() -> list:
    return [
        amqp_relation_complete(),
        identity_credentials_relation_complete(),
        ovsdb_cms_relation(),
        nova_service_relation(),
    ]


def _all_secrets() -> list:
    return [identity_credentials_secret()]


# ---- Fixtures ----


@pytest.fixture()
def _mock_heavy_externals(monkeypatch):
    """Patch external modules that cannot run in a unit-test environment.

    This replaces the heavy mock.patch list from the harness tests:
    socket, snap, get_local_ip_by_default_route, subprocess,
    ConsulNotifyRequirer, epa_client.
    """
    # snap module
    mock_snap = MagicMock()
    mock_snap.SnapError = Exception
    mock_snap.SnapNotFoundError = Exception
    mock_snap.SnapState.Latest = "latest"
    hypervisor_snap = MagicMock()
    hypervisor_snap.present = False
    hypervisor_snap.get.return_value = {}
    epa_orchestrator_snap = MagicMock()
    epa_orchestrator_snap.present = False
    microovn_snap = MagicMock()
    microovn_snap.present = False
    mock_snap.SnapCache.return_value = {
        "openstack-hypervisor": hypervisor_snap,
        "epa-orchestrator": epa_orchestrator_snap,
        "microovn": microovn_snap,
    }
    mock_snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(charm, "snap", mock_snap)

    # socket module
    mock_socket = MagicMock()
    mock_socket.getfqdn.return_value = "test.local"
    mock_socket.gethostname.return_value = "test"
    monkeypatch.setattr(charm, "socket", mock_socket)

    # subprocess
    mock_subprocess = MagicMock()
    monkeypatch.setattr(charm, "subprocess", mock_subprocess)

    # get_local_ip_by_default_route
    monkeypatch.setattr(
        charm, "get_local_ip_by_default_route", lambda: "10.0.0.10"
    )

    # epa_client
    mock_epa = MagicMock()
    mock_epa.EPAClient.return_value.is_available.return_value = False
    monkeypatch.setattr(charm, "epa_client", mock_epa)

    # os.system / os.path.exists used for OVS cleanup
    monkeypatch.setattr(charm.os, "system", lambda *a, **kw: 0)
    monkeypatch.setattr(charm.os.path, "exists", lambda p: False)


@pytest.fixture()
def ctx(_mock_heavy_externals):
    """Create a testing.Context for HypervisorOperatorCharm."""
    return testing.Context(
        charm.HypervisorOperatorCharm,
        charm_root=CHARM_ROOT,
    )


@pytest.fixture()
def complete_relations():
    """All mandatory relations needed to reach active status."""
    return _all_mandatory_relations()


@pytest.fixture()
def complete_secrets():
    """All secrets needed by complete relations."""
    return _all_secrets()


@pytest.fixture()
def complete_state(complete_relations, complete_secrets):
    """Full state with leader and all mandatory relations + secrets."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        secrets=complete_secrets,
    )
