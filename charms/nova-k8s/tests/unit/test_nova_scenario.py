#!/usr/bin/env python3

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

"""Scenario tests for nova-k8s charm.

Nova is the most complex K8s charm: 4 containers, 3 databases, placement
relation, traefik-route relations, and multi-step db-sync commands.
"""

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
    assert_config_file_contains,
    db_credentials_secret,
    db_relation_complete,
    identity_service_relation_complete,
    identity_service_secret,
    ingress_relation_complete,
    mandatory_relations_from_charmcraft,
    peer_relation,
    traefik_route_relation_complete,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# ---------------------------------------------------------------------------
# Secret IDs – one per database + one for identity-service
# ---------------------------------------------------------------------------
DB_SECRET_ID = "secret:db-creds"
API_DB_SECRET_ID = "secret:api-db-creds"
CELL_DB_SECRET_ID = "secret:cell-db-creds"
ID_SECRET_ID = "secret:id-svc-creds"

# ---------------------------------------------------------------------------
# Mandatory relations (non-optional requires from charmcraft.yaml)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Container names
# ---------------------------------------------------------------------------
CONTAINERS = [
    "nova-api",
    "nova-scheduler",
    "nova-conductor",
    "nova-spiceproxy",
]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _nova_api_container(can_connect: bool = True) -> testing.Container:
    """Nova API container with exec mocks for a2ensite, a2dissite, db-sync."""
    return testing.Container(
        name="nova-api",
        can_connect=can_connect,
        execs=[
            testing.Exec(command_prefix=["a2dissite"], return_code=0),
            testing.Exec(command_prefix=["a2ensite"], return_code=0),
            testing.Exec(command_prefix=["sudo"], return_code=0),
            testing.Exec(
                command_prefix=["/root/cell_create_wrapper.sh"],
                return_code=0,
            ),
        ],
    )


def _service_container(
    name: str, can_connect: bool = True
) -> testing.Container:
    """Non-WSGI service container (scheduler, conductor, spiceproxy)."""
    return testing.Container(name=name, can_connect=can_connect)


def _all_containers(can_connect: bool = True) -> list[testing.Container]:
    return [
        _nova_api_container(can_connect=can_connect),
        _service_container("nova-scheduler", can_connect=can_connect),
        _service_container("nova-conductor", can_connect=can_connect),
        _service_container("nova-spiceproxy", can_connect=can_connect),
    ]


def _placement_relation() -> testing.Relation:
    """Placement service-readiness relation (complete)."""
    return testing.Relation(
        endpoint="placement",
        remote_app_name="placement",
        remote_app_data={"ready": "true"},
        remote_units_data={0: {}},
    )


def _all_secrets() -> list[testing.Secret]:
    return [
        db_credentials_secret(secret_id=DB_SECRET_ID),
        db_credentials_secret(secret_id=API_DB_SECRET_ID),
        db_credentials_secret(secret_id=CELL_DB_SECRET_ID),
        identity_service_secret(secret_id=ID_SECRET_ID),
    ]


def _all_relations() -> list:
    return [
        db_relation_complete(endpoint="database", secret_id=DB_SECRET_ID),
        db_relation_complete(
            endpoint="api-database", secret_id=API_DB_SECRET_ID
        ),
        db_relation_complete(
            endpoint="cell-database", secret_id=CELL_DB_SECRET_ID
        ),
        amqp_relation_complete(),
        identity_service_relation_complete(secret_id=ID_SECRET_ID),
        ingress_relation_complete(endpoint="ingress-internal"),
        traefik_route_relation_complete(endpoint="traefik-route-internal"),
        traefik_route_relation_complete(endpoint="traefik-route-public"),
        _placement_relation(),
        peer_relation(),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def ctx():
    """Ctx."""
    return testing.Context(
        charm.NovaOperatorCharm,
        charm_root=CHARM_ROOT,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """Test blocked when no relations."""
        state_in = testing.State(
            leader=True,
            containers=_all_containers(can_connect=False),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)
        assert "integration missing" in state_out.unit_status.message


class TestPebbleReady:
    """Pebble-ready fires for each of the 4 containers."""

    @pytest.mark.parametrize("container_name", CONTAINERS)
    def test_pebble_ready_handler(self, ctx, container_name):
        """Each container's pebble-ready event fires without error."""
        containers = _all_containers()
        target = [c for c in containers if c.name == container_name][0]
        state_in = testing.State(
            leader=True,
            relations=_all_relations(),
            containers=containers,
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.pebble_ready(target), state_in)

        # Should reach active once all relations are present
        assert state_out.unit_status == testing.ActiveStatus("")


class TestAllRelations:
    """Config-changed with all required relations complete → active."""

    def test_all_relations(self, ctx):
        """Test all relations."""
        state_in = testing.State(
            leader=True,
            relations=_all_relations(),
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")

        # Verify nova-api container got configured with pebble layer
        out_container = state_out.get_container("nova-api")
        assert "nova-api" in out_container.layers
        layer = out_container.layers["nova-api"]
        assert "wsgi-nova-api" in layer.to_dict().get("services", {})

    def test_config_files_written(self, ctx):
        """Config files are rendered into the nova-api container."""
        state_in = testing.State(
            leader=True,
            relations=_all_relations(),
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("nova-api")
        fs = out_container.get_filesystem(ctx)
        nova_conf = fs / "etc" / "nova" / "nova.conf"
        assert nova_conf.exists(), "nova.conf not written to container"

    def test_nova_conf_contains_admin_cinder_client(self, ctx):
        """nova.conf renders an explicit admin Cinder client section."""
        state_in = testing.State(
            leader=True,
            relations=_all_relations(),
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_config_file_contains(
            state_out,
            ctx,
            "nova-api",
            "/etc/nova/nova.conf",
            [
                "[cinder]",
                "catalog_info = volumev3:cinderv3:adminURL",
                "os_region_name = RegionOne",
            ],
        )


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation → blocked/waiting."""

    @pytest.mark.parametrize("missing_rel", sorted(MANDATORY_RELATIONS))
    def test_blocked_when_relation_missing(self, ctx, missing_rel):
        """Test blocked when relation missing."""
        remaining = [r for r in _all_relations() if r.endpoint != missing_rel]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader_no_metadata_secret(self, ctx):
        """Non-leader without metadata secret returns early (maintenance)."""
        state_in = testing.State(
            leader=False,
            relations=_all_relations(),
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Non-leader returns before configure_charm runs fully because
        # the shared metadata secret is not yet in peer app data.
        assert state_out.unit_status.name in ("maintenance", "waiting")

    def test_waiting_non_leader_with_metadata_secret(self, ctx):
        """Non-leader with metadata secret but no leader_ready → waiting."""
        # Build peer relation with metadata secret set (as leader would)
        peers = testing.PeerRelation(
            endpoint="peers",
            local_app_data={"shared-metadata-secret": "fake-uuid"},
        )
        relations = [r for r in _all_relations() if r.endpoint != "peers"] + [
            peers
        ]

        state_in = testing.State(
            leader=False,
            relations=relations,
            containers=_all_containers(),
            secrets=_all_secrets(),
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message
