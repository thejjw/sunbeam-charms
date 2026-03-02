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

"""Scenario (ops.testing state-transition) tests for sunbeam-ovn-proxy."""

from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
)


class TestAllRelations:
    """With all required relations complete the charm reaches active."""

    def test_active_with_ovsdb_relation(self, ctx, complete_state):
        """Config-changed with ovsdb relation ready → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_active_with_ovsdb_and_ovsdb_cms(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Config-changed with both ovsdb and ovsdb-cms → ActiveStatus."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx):
        """No relations at all → blocked with 'integration missing'."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", "integration missing")


class TestWaitingWhenOvsdbNotReady:
    """Ovsdb relation present but incomplete → waiting."""

    def test_waiting_when_ovsdb_empty(self, ctx, ovsdb_relation_empty):
        """Ovsdb relation with no data → WaitingStatus."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_empty],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.WaitingStatus)

    def test_waiting_when_ovsdb_empty_with_ovsdb_cms(
        self, ctx, ovsdb_relation_empty, ovsdb_cms_relation
    ):
        """Ovsdb not ready + ovsdb-cms present → WaitingStatus."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_empty, ovsdb_cms_relation],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.WaitingStatus)


class TestDataPropagation:
    """Verify data flows from ovsdb to ovsdb-cms."""

    def test_leader_propagates_data_to_ovsdb_cms(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Leader writes connection strings to ovsdb-cms app data."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_complete), state_in
        )
        assert state_out.unit_status == testing.ActiveStatus("")

        app_data = state_out.get_relations("ovsdb-cms")[0].local_app_data
        assert app_data == {
            "db_nb_connection_strs": "tcp:127.0.0.1:6641",
            "db_sb_connection_strs": "tcp:127.0.0.1:6641",
            "proxy-relation": "true",
        }

    def test_non_leader_does_not_write_app_data(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Non-leader does not write ovsdb-cms app data."""
        state_in = testing.State(
            leader=False,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_complete), state_in
        )
        assert state_out.unit_status == testing.ActiveStatus("")
        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {}

    def test_propagated_payload_shape(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Verify propagated payload has exactly the expected keys and values."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_complete), state_in
        )

        app_data = state_out.get_relations("ovsdb-cms")[0].local_app_data
        assert "db_nb_connection_strs" in app_data
        assert "db_sb_connection_strs" in app_data
        assert "proxy-relation" in app_data
        assert len(app_data) == 3


class TestOvsdbRemoval:
    """Verify ovsdb removal clears ovsdb-cms data."""

    def test_ovsdb_broken_clears_ovsdb_cms_data(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Removing ovsdb relation clears ovsdb-cms app data."""
        # Phase 1: Populate ovsdb-cms data
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_with_data = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_complete), state_in
        )

        # Verify data was populated
        assert state_with_data.get_relations("ovsdb-cms")[
            0
        ].local_app_data == {
            "db_nb_connection_strs": "tcp:127.0.0.1:6641",
            "db_sb_connection_strs": "tcp:127.0.0.1:6641",
            "proxy-relation": "true",
        }

        # Phase 2: Break ovsdb relation
        ovsdb_from_state = state_with_data.get_relations("ovsdb")[0]
        ovsdb_cms_with_data = state_with_data.get_relations("ovsdb-cms")[0]
        state_for_broken = testing.State(
            leader=True,
            relations=[ovsdb_from_state, ovsdb_cms_with_data],
        )

        state_out = ctx.run(
            ctx.on.relation_broken(ovsdb_from_state), state_for_broken
        )

        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {}
        assert_unit_status(state_out, "blocked", "integration missing")

    def test_ovsdb_not_ready_clears_ovsdb_cms_data(
        self, ctx, ovsdb_relation_empty, ovsdb_cms_relation
    ):
        """When ovsdb is not ready, ovsdb-cms data contains only proxy flag."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_empty, ovsdb_cms_relation],
        )
        state_out = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_empty), state_in
        )

        app_data = state_out.get_relations("ovsdb-cms")[0].local_app_data
        assert app_data == {"proxy-relation": "true"}
        assert isinstance(state_out.unit_status, testing.WaitingStatus)


class TestOvsdbCmsCallbacks:
    """Verify ovsdb-cms relation events trigger configuration."""

    def test_ovsdb_cms_joined_propagates_data(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """ovsdb-cms relation_joined triggers data propagation."""
        stored_state = testing.StoredState(
            name="_state",
            owner_path="SunbeamOvnProxyCharm",
            content={"unit_bootstrapped": True},
        )
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
            stored_states=[stored_state],
        )
        state_out = ctx.run(
            ctx.on.relation_joined(ovsdb_cms_relation), state_in
        )
        assert state_out.unit_status == testing.ActiveStatus("")
        assert (
            "proxy-relation"
            in state_out.get_relations("ovsdb-cms")[0].local_app_data
        )

    def test_ovsdb_cms_joined_non_leader_no_app_data(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """Non-leader does not write app data on ovsdb-cms join."""
        stored_state = testing.StoredState(
            name="_state",
            owner_path="SunbeamOvnProxyCharm",
            content={"unit_bootstrapped": True},
        )
        state_in = testing.State(
            leader=False,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
            stored_states=[stored_state],
        )
        state_out = ctx.run(
            ctx.on.relation_joined(ovsdb_cms_relation), state_in
        )
        assert state_out.unit_status == testing.ActiveStatus("")
        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {}

    def test_ovsdb_cms_departed(
        self, ctx, ovsdb_relation_complete, ovsdb_cms_relation
    ):
        """ovsdb-cms departed → charm remains active (ovsdb still present)."""
        state_in = testing.State(
            leader=True,
            relations=[ovsdb_relation_complete, ovsdb_cms_relation],
        )
        state_with_data = ctx.run(
            ctx.on.relation_changed(ovsdb_relation_complete), state_in
        )

        ovsdb_cms_with_data = state_with_data.get_relations("ovsdb-cms")[0]
        state_out = ctx.run(
            ctx.on.relation_departed(ovsdb_cms_with_data), state_with_data
        )
        assert state_out.unit_status == testing.ActiveStatus("")
