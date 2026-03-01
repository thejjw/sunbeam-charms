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

"""Shared fixtures for cinder-volume-ceph unit tests (ops.testing)."""

import json
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
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]

# ---- relation builders for cinder-volume-ceph-specific endpoints ----

CEPH_CLIENT_UNIT = "cinder-volume-ceph-0"


def _ceph_broker_req_id() -> tuple[str, str]:
    """Build a CephBrokerRq with empty ops and return (request_id, json).

    The interface hashes the request content to derive the id, so we
    must use the real library to get a matching value.
    """
    from charmhelpers.contrib.storage.linux.ceph import (
        CephBrokerRq,
    )

    rq = CephBrokerRq(api_version=1)
    rq.set_ops([])
    return rq.request_id, rq.request


def ceph_relation_complete() -> testing.Relation:
    """Ceph relation with auth, key, and matching broker request/response."""
    request_id, broker_req_json = _ceph_broker_req_id()
    return testing.Relation(
        endpoint="ceph",
        remote_app_name="ceph-mon",
        remote_app_data={},
        local_unit_data={
            "broker_req": broker_req_json,
        },
        remote_units_data={
            0: {
                "auth": "cephx",
                "key": "AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==",
                "ingress-address": "192.0.2.2",
                "ceph-public-address": "192.0.2.2",
                f"broker-rsp-{CEPH_CLIENT_UNIT}": json.dumps(
                    {
                        "exit-code": 0,
                        "request-id": request_id,
                    }
                ),
            },
            1: {},
        },
    )


def cinder_volume_relation() -> testing.SubordinateRelation:
    """cinder-volume subordinate relation with snap-name data."""
    return testing.SubordinateRelation(
        endpoint="cinder-volume",
        remote_app_name="cinder-volume",
        remote_unit_data={"snap-name": "cinder-volume"},
    )


def _all_mandatory_relations() -> list:
    return [
        ceph_relation_complete(),
        cinder_volume_relation(),
    ]


# ---- Fixtures ----


@pytest.fixture(autouse=True)
def mock_snap(monkeypatch):
    """Patch snap module so snap operations are no-ops in tests."""
    snap = MagicMock()
    snap.SnapError = Exception
    snap.SnapNotFoundError = Exception
    snap.SnapState.Latest = "latest"
    cinder_volume_snap = MagicMock()
    cinder_volume_snap.present = False
    snap.SnapCache.return_value = {
        "cinder-volume": cinder_volume_snap,
    }
    snap.SnapClient.return_value.get_installed_snaps.return_value = []
    monkeypatch.setattr(
        charm.CinderVolumeCephOperatorCharm,
        "_import_snap",
        lambda self: snap,
    )
    return snap


@pytest.fixture()
def ctx():
    """Create a testing.Context for CinderVolumeCephOperatorCharm."""
    return testing.Context(
        charm.CinderVolumeCephOperatorCharm,
        charm_root=CHARM_ROOT,
    )


@pytest.fixture()
def complete_relations():
    """All mandatory relations needed to reach active status."""
    return _all_mandatory_relations()


@pytest.fixture()
def complete_state(complete_relations):
    """Full state with leader, all mandatory relations + peer."""
    return testing.State(
        leader=True,
        relations=[*complete_relations, peer_relation()],
    )
