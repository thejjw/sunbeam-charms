# Copyright 2026 Canonical Ltd.
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

"""Unit tests for SunbeamOvnProxyCharm.

This test module validates the OVSDB proxy charm's behavior, including:
- Relation lifecycle management (ovsdb and ovsdb-cms)
- Data propagation from ovsdb to ovsdb-cms relations
- Leader vs non-leader behavior
- Status handling for various relation states
- Data clearing on relation removal
"""

import unittest

from charm import (
    SunbeamOvnProxyCharm,
)
from ops import (
    testing,
)


class TestSunbeamOvnProxyCharm(unittest.TestCase):
    """Charm test class for SunbeamOvnProxyCharm."""

    def setUp(self):
        """Set up test case."""
        self.ctx = testing.Context(SunbeamOvnProxyCharm)
        self.ovsdb = testing.Relation(
            endpoint="ovsdb",
            interface="ovsdb",
            remote_app_data={
                "db_nb_connection_str": "tcp:127.0.0.1:6641",
                "db_sb_connection_str": "tcp:127.0.0.1:6641",
            },
        )

    def test_charm_initial_status(self):
        """Test charm reports blocked status when no ovsdb relation exists."""
        state_in = testing.State()

        state_out = self.ctx.run(self.ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.BlockedStatus(
            "(ovsdb) integration missing"
        )

    def test_charm_related_to_ovsdb_not_ready(self):
        """Test charm reports waiting status when ovsdb relation exists but has no data."""
        relation = testing.Relation(endpoint="ovsdb", interface="ovsdb")
        state_in = testing.State(relations={relation})

        state_out = self.ctx.run(self.ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.WaitingStatus(
            "(workload) Not all relations are ready"
        )

    def test_charm_related_to_ovsdb_ready_no_ovsdb_cms(self):
        """Test charm reports active status when ovsdb relation is ready without ovsdb-cms."""
        state_in = testing.State(relations={self.ovsdb})

        state_out = self.ctx.run(self.ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus()

    def test_charm_ovsdb_updated_ovsdb_cms_related(self):
        """Test ovsdb relation change propagates data to ovsdb-cms (leader only).

        Leader unit writes connection strings and proxy-relation flag to ovsdb-cms app data.
        Non-leader units do not write any app data.
        """
        for leader in [True, False]:
            with self.subTest(leader=leader):
                ovsdb_cms = testing.Relation(
                    endpoint="ovsdb-cms",
                    interface="ovsdb-cms",
                )
                state_in = testing.State(
                    relations={self.ovsdb, ovsdb_cms}, leader=leader
                )
                state_out = self.ctx.run(
                    self.ctx.on.relation_changed(self.ovsdb), state_in
                )
                assert state_out.unit_status == testing.ActiveStatus()
                if leader:
                    assert state_out.get_relations("ovsdb-cms")[
                        0
                    ].local_app_data == {
                        "db_nb_connection_strs": "tcp:127.0.0.1:6641",
                        "db_sb_connection_strs": "tcp:127.0.0.1:6641",
                        "proxy-relation": "true",
                    }
                else:
                    assert (
                        state_out.get_relations("ovsdb-cms")[0].local_app_data
                        == {}
                    )

    def test_charm_ovsdb_removed_clears_ovsdb_cms_data(self):
        """Two-phase test: populate data, then remove ovsdb and verify clearing."""
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        # Phase 1: Populate ovsdb-cms app data with both relations present
        state_in = testing.State(
            relations={self.ovsdb, ovsdb_cms}, leader=True
        )
        state_with_data = self.ctx.run(
            self.ctx.on.relation_changed(self.ovsdb), state_in
        )

        # Verify data was populated
        assert state_with_data.get_relations("ovsdb-cms")[
            0
        ].local_app_data == {
            "db_nb_connection_strs": "tcp:127.0.0.1:6641",
            "db_sb_connection_strs": "tcp:127.0.0.1:6641",
            "proxy-relation": "true",
        }

        # Phase 2: Trigger relation_broken for ovsdb (relation still in state during broken event)
        ovsdb_from_state = state_with_data.get_relations("ovsdb")[0]
        ovsdb_cms_with_data = state_with_data.get_relations("ovsdb-cms")[0]
        state_for_broken = testing.State(
            relations={ovsdb_from_state, ovsdb_cms_with_data},
            leader=True,
        )

        state_out = self.ctx.run(
            self.ctx.on.relation_broken(ovsdb_from_state), state_for_broken
        )

        # Verify ovsdb-cms data was cleared
        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {}
        assert state_out.unit_status == testing.BlockedStatus(
            "(ovsdb) integration missing"
        )

    def test_charm_ovsdb_present_not_ready_ovsdb_cms_absent(self):
        """Test _update_ovsdb_cms_data early exit when ovsdb not ready and no ovsdb-cms."""
        ovsdb_not_ready = testing.Relation(
            endpoint="ovsdb",
            interface="ovsdb",
            remote_app_data={},  # No connection strings = not ready
        )
        state_in = testing.State(relations={ovsdb_not_ready})

        state_out = self.ctx.run(
            self.ctx.on.relation_changed(ovsdb_not_ready), state_in
        )
        # Should be waiting since relation exists but not ready
        assert state_out.unit_status == testing.WaitingStatus(
            "(ovsdb) integration incomplete"
        )

    def test_charm_ovsdb_present_not_ready_ovsdb_cms_present(self):
        """Test _update_ovsdb_cms_data early exit: ovsdb not ready, ovsdb-cms present."""
        ovsdb_not_ready = testing.Relation(
            endpoint="ovsdb",
            interface="ovsdb",
            remote_app_data={},  # No connection strings = not ready
        )
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        state_in = testing.State(
            relations={ovsdb_not_ready, ovsdb_cms}, leader=True
        )

        state_out = self.ctx.run(
            self.ctx.on.relation_changed(ovsdb_not_ready), state_in
        )

        # ovsdb-cms app data should only have proxy-relation flag
        # (no connection strings when ovsdb not ready)
        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {
            "proxy-relation": "true",
        }
        assert state_out.unit_status == testing.WaitingStatus(
            "(ovsdb) integration incomplete"
        )

    def test_charm_ovsdb_cms_ready_callback_triggers_configure(self):
        """Test ovsdb-cms ready callback triggers configure_charm and propagates data."""
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        # Include stored state to simulate bootstrapped unit
        stored_state = testing.StoredState(
            name="_state",
            owner_path="SunbeamOvnProxyCharm",
            content={"unit_bootstrapped": True},
        )
        state_in = testing.State(
            relations={self.ovsdb, ovsdb_cms},
            leader=True,
            stored_states={stored_state},
        )

        # Trigger via ovsdb-cms relation_joined (simulates ready callback path)
        state_out = self.ctx.run(
            self.ctx.on.relation_joined(ovsdb_cms), state_in
        )

        assert state_out.unit_status == testing.ActiveStatus()
        # On relation_joined, the ovsdb-cms interface sets proxy-relation flag
        # Full data propagation happens on ovsdb relation_changed event
        assert (
            "proxy-relation"
            in state_out.get_relations("ovsdb-cms")[0].local_app_data
        )

    def test_charm_ovsdb_cms_ready_callback_non_leader(self):
        """Test ovsdb-cms ready callback as non-leader does not write app data."""
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        # Include stored state to simulate bootstrapped unit
        stored_state = testing.StoredState(
            name="_state",
            owner_path="SunbeamOvnProxyCharm",
            content={"unit_bootstrapped": True},
        )
        state_in = testing.State(
            relations={self.ovsdb, ovsdb_cms},
            leader=False,
            stored_states={stored_state},
        )

        state_out = self.ctx.run(
            self.ctx.on.relation_joined(ovsdb_cms), state_in
        )

        assert state_out.unit_status == testing.ActiveStatus()
        # Non-leader should not write app data
        assert state_out.get_relations("ovsdb-cms")[0].local_app_data == {}

    def test_charm_ovsdb_cms_goneaway_clears_data(self):
        """Test ovsdb-cms relation removal lifecycle."""
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        # First populate the data
        state_in = testing.State(
            relations={self.ovsdb, ovsdb_cms}, leader=True
        )
        state_with_data = self.ctx.run(
            self.ctx.on.relation_changed(self.ovsdb), state_in
        )

        # Verify data was set
        assert state_with_data.get_relations("ovsdb-cms")[
            0
        ].local_app_data == {
            "db_nb_connection_strs": "tcp:127.0.0.1:6641",
            "db_sb_connection_strs": "tcp:127.0.0.1:6641",
            "proxy-relation": "true",
        }

        # Trigger relation_departed for ovsdb-cms
        ovsdb_cms_with_data = state_with_data.get_relations("ovsdb-cms")[0]
        state_out = self.ctx.run(
            self.ctx.on.relation_departed(ovsdb_cms_with_data),
            state_with_data,
        )

        # Status should remain active since ovsdb is still present
        assert state_out.unit_status == testing.ActiveStatus()

    def test_charm_propagated_payload_shape(self):
        """Verify propagated payload matches ovsdb.context() expectations."""
        ovsdb_cms = testing.Relation(
            endpoint="ovsdb-cms",
            interface="ovsdb-cms",
        )
        state_in = testing.State(
            relations={self.ovsdb, ovsdb_cms}, leader=True
        )
        state_out = self.ctx.run(
            self.ctx.on.relation_changed(self.ovsdb), state_in
        )

        app_data = state_out.get_relations("ovsdb-cms")[0].local_app_data

        # Verify all expected keys are present
        assert "db_nb_connection_strs" in app_data
        assert "db_sb_connection_strs" in app_data
        assert "proxy-relation" in app_data

        # Verify values match expected format
        assert app_data["db_nb_connection_strs"] == "tcp:127.0.0.1:6641"
        assert app_data["db_sb_connection_strs"] == "tcp:127.0.0.1:6641"
        assert app_data["proxy-relation"] == "true"

        # Verify no unexpected keys
        assert len(app_data) == 3
