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

"""Scenario (ops.testing state-transition) tests for sunbeam-clusterd."""

import pytest
from charms.operator_libs_linux.v2 import (
    snap,
)
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
    certificates_relation_complete,
    tracing_relation_complete,
)


class TestLeaderBootstrap:
    """Leader with peers relation bootstraps and reaches active."""

    def test_active_with_peers(self, ctx, complete_state, _mock_clusterd):
        """Config-changed as leader with peers → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")
        _mock_clusterd.bootstrap.assert_called_once()

    def test_clusterd_ready_called(self, ctx, complete_state, _mock_clusterd):
        """Leader checks clusterd readiness before bootstrap."""
        ctx.run(ctx.on.config_changed(), complete_state)
        _mock_clusterd.ready.assert_called()


class TestNonLeaderWaits:
    """Non-leader unit waits for leader readiness."""

    def test_waiting_non_leader(self, ctx, peers):
        """Non-leader without leader_ready → WaitingStatus."""
        state_in = testing.State(
            leader=False,
            relations=[peers],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "Leader not ready")

    def test_non_leader_does_not_bootstrap(self, ctx, peers, _mock_clusterd):
        """Non-leader must not call bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=[peers],
        )
        ctx.run(ctx.on.config_changed(), state_in)
        _mock_clusterd.bootstrap.assert_not_called()


class TestNoPeersBlocked:
    """Without peer relation the charm is blocked."""

    def test_blocked_no_relations(self, ctx):
        """No relations at all → blocked."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting")


class TestWithCertificates:
    """With certificates relation present but incomplete, charm reports waiting."""

    def test_waiting_when_certs_incomplete(self, ctx, peers):
        """Leader with peers + incomplete certificates → waiting for certs."""
        state_in = testing.State(
            leader=True,
            relations=[peers, certificates_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "certificates")

    def test_no_certs_pushed_when_incomplete(self, ctx, peers, _mock_clusterd):
        """Certs not pushed to clusterd when certificate data is incomplete."""
        state_in = testing.State(
            leader=True,
            relations=[peers, certificates_relation_complete()],
        )
        ctx.run(ctx.on.config_changed(), state_in)
        _mock_clusterd.set_certs.assert_not_called()


class TestWithTracing:
    """With tracing relation present, charm still reaches active."""

    def test_active_with_tracing(self, ctx, peers):
        """Leader with peers + tracing → active."""
        state_in = testing.State(
            leader=True,
            relations=[peers, tracing_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestConfigChanged:
    """Config-changed event updates snap settings."""

    def test_config_changed_custom_channel(self, ctx, peers, _mock_snap):
        """Config-changed with custom snap-channel."""
        state_in = testing.State(
            leader=True,
            relations=[peers],
            config={"snap-channel": "edge"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestGetCredentialsAction:
    """get-credentials action returns correct URL."""

    def test_get_credentials(self, ctx, complete_state, _mock_clusterd):
        """Action returns URL with binding address."""
        _mock_clusterd.bootstrap.return_value = None
        # First bootstrap the charm so peers.interface.state.joined is True
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        ctx_out = ctx.run(ctx.on.action("get-credentials"), state_out)
        assert ctx_out.unit_status == testing.ActiveStatus("")


class TestSnapNotFound:
    """Install event when snap is not present raises an error."""

    def test_snap_not_installed_raises(self, ctx, _mock_snap):
        """Install raises when snap is not found."""
        mock_openstack = _mock_snap["openstack"]
        mock_openstack.present = False
        mock_openstack.ensure.side_effect = snap.SnapNotFoundError("openstack")
        state_in = testing.State(leader=True)
        with pytest.raises(Exception, match="SnapInstallationError"):
            ctx.run(ctx.on.install(), state_in)
