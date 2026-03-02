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

"""Pure unit tests for EpaOrchestratorCharm properties and no-op methods."""

from ops import (
    testing,
)


class TestSnapNameProperty:
    """snap_name property returns config value."""

    def test_snap_name_default(self, ctx):
        """Default snap-name config is returned by snap_name property."""
        state_in = testing.State(leader=True)
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            assert mgr.charm.snap_name == "epa-orchestrator"

    def test_snap_name_custom(self, ctx):
        """Custom snap-name config is returned by snap_name property."""
        state_in = testing.State(
            leader=True, config={"snap-name": "custom-epa"}
        )
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            assert mgr.charm.snap_name == "custom-epa"


class TestSnapChannelProperty:
    """snap_channel property returns config value."""

    def test_snap_channel_configured(self, ctx):
        """Configured snap-channel is returned by snap_channel property."""
        state_in = testing.State(
            leader=True, config={"snap-channel": "latest/edge"}
        )
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            assert mgr.charm.snap_channel == "latest/edge"


class TestNoOpMethods:
    """ensure_services_running and stop_services are no-ops."""

    def test_ensure_services_running_returns_none(self, ctx):
        """ensure_services_running returns None (no-op)."""
        state_in = testing.State(leader=True)
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            result = mgr.charm.ensure_services_running(False)
            assert result is None

    def test_stop_services_returns_none(self, ctx):
        """stop_services returns None (no-op)."""
        state_in = testing.State(leader=True)
        with ctx(ctx.on.config_changed(), state_in) as mgr:
            result = mgr.charm.stop_services()
            assert result is None
