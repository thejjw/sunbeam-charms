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

"""Scenario (ops.testing state-transition) tests for glance-k8s."""

from pathlib import (
    Path,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_exists,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    k8s_api_container,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)


class TestAllRelations:
    """With all relations complete the charm reaches active and configures the service."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_file_written(self, ctx, complete_state):
        """All relations present → glance-api.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "glance-api", "/etc/glance/glance-api.conf"
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the service."""
        container = complete_state.get_container("glance-api")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("glance-api")
        assert "glance-api" in out_container.layers
        layer = out_container.layers["glance-api"]
        assert "glance-api" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("glance-api") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_without_relations_blocked(self, ctx):
        """Pebble-ready but no relations → blocked."""
        container = k8s_api_container(
            "glance-api",
            extra_execs=[
                testing.Exec(command_prefix=["a2enmod"], return_code=0),
                testing.Exec(command_prefix=["ceph-authtool"], return_code=0),
                testing.Exec(command_prefix=["chown"], return_code=0),
                testing.Exec(command_prefix=["chmod"], return_code=0),
            ],
        )
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        container = k8s_api_container("glance-api", can_connect=False)
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

    @pytest.mark.parametrize(
        "missing_rel",
        sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_relation_missing(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        container,
        ceph_stored_state,
        missing_rel,
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=[container],
            secrets=complete_secrets,
            stored_states=[ceph_stored_state],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


class TestEnabledBackends:
    """Verify enabled_backends config in glance-api.conf with ceph and ceph-rgw."""

    def test_enabled_backends_with_ceph(self, ctx, complete_state):
        """With ceph relation, enabled_backends includes filestore and ceph."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        container_out = state_out.get_container("glance-api")
        fs = container_out.get_filesystem(ctx)
        content = (fs / "etc/glance/glance-api.conf").read_text()

        for expected in [
            "enabled_backends = filestore:file,ceph:rbd\n",
            "[ceph]",
            "rbd_store_chunk_size = 8",
            "rbd_store_pool = glance",
            "rbd_store_user = glance",
            "rados_connect_timeout = 0",
            "rbd_store_ceph_conf = /etc/ceph/ceph.conf",
        ]:
            assert expected in content, f"{expected!r} not in glance-api.conf"

    def test_enabled_backends_with_ceph_and_rgw(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        container,
        ceph_stored_state,
    ):
        """With ceph + ceph-rgw-ready, enabled_backends also includes swift."""
        rgw_relation = testing.Relation(
            endpoint="ceph-rgw-ready",
            remote_app_name="microceph",
            remote_app_data={"ready": "true"},
        )
        state_in = testing.State(
            leader=True,
            relations=complete_relations + [rgw_relation],
            containers=[container],
            secrets=complete_secrets,
            stored_states=[ceph_stored_state],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        container_out = state_out.get_container("glance-api")
        fs = container_out.get_filesystem(ctx)

        content = (fs / "etc/glance/glance-api.conf").read_text()
        for expected in [
            "enabled_backends = filestore:file,ceph:rbd,swift:swift\n",
            "[ceph]",
            "rbd_store_chunk_size = 8",
            "rbd_store_pool = glance",
            "rbd_store_user = glance",
            "rados_connect_timeout = 0",
            "rbd_store_ceph_conf = /etc/ceph/ceph.conf",
            "[swift]",
            "swift_store_config_file = /etc/glance/glance-api.d/01-swift.conf",
            "swift_store_region = RegionOne",
            "swift_store_container = glance",
            "swift_store_create_container_on_put = True",
        ]:
            assert expected in content, f"{expected!r} not in glance-api.conf"

        swift_content = (
            fs / "etc/glance/glance-api.d/01-swift.conf"
        ).read_text()
        for expected in [
            "[ref1]",
            "auth_address = http://keystone.internal:5000",
            "auth_version = 3",
            "user = svc-project:svcuser1",
            "key = svcpass1",
            "project_domain_name = svc-domain",
            "user_domain_name = svc-domain",
        ]:
            assert (
                expected in swift_content
            ), f"{expected!r} not in 01-swift.conf"


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        container,
        ceph_stored_state,
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            stored_states=[ceph_stored_state],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message


class TestContainerDisconnectBlocksOrWaits:
    """Config-changed with disconnected containers → blocked/waiting."""

    def test_container_disconnect(self, ctx, complete_state):
        """Charm should block/wait when containers cannot connect."""
        assert_container_disconnect_causes_waiting_or_blocked(
            ctx, complete_state
        )


class TestRelationBrokenBlocksOrWaits:
    """Breaking each mandatory relation → blocked/waiting."""

    @pytest.mark.parametrize(
        "relation_endpoint",
        sorted(MANDATORY_RELATIONS),
    )
    def test_relation_broken(self, ctx, complete_state, relation_endpoint):
        """Charm should block/wait when a mandatory relation is broken."""
        assert_relation_broken_causes_blocked_or_waiting(
            ctx, complete_state, relation_endpoint
        )
