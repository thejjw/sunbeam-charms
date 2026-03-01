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

"""Scenario (ops.testing state-transition) tests for sunbeam-libs."""

from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
)


class TestPebbleReady:
    """Pebble-ready event triggers configure_charm."""

    def test_pebble_ready_with_container(self, ctx, container):
        """Pebble-ready with connectable container → active."""
        state_in = testing.State(
            leader=True,
            containers=[container],
        )
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_pebble_ready_non_leader(self, ctx, container):
        """Pebble-ready as non-leader → still active (no peers required)."""
        state_in = testing.State(
            leader=False,
            containers=[container],
        )
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)
        # Placeholder charm has no peer relation, so non-leader also goes active
        assert state_out.unit_status == testing.ActiveStatus("")


class TestConfigChanged:
    """Config-changed event for the placeholder charm."""

    def test_config_changed_leader_active(self, ctx, container):
        """Config-changed as leader with container → active."""
        state_in = testing.State(
            leader=True,
            containers=[container],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_changed_container_not_ready(self, ctx):
        """Config-changed with container not connectable → waiting."""
        from ops_sunbeam.test_utils_scenario import (
            k8s_container,
        )

        container = k8s_container("placeholder", can_connect=False)
        state_in = testing.State(
            leader=True,
            containers=[container],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting")


class TestInstall:
    """Install event for the placeholder charm."""

    def test_install_event(self, ctx, container):
        """Install event runs without error."""
        state_in = testing.State(
            leader=True,
            containers=[container],
        )
        state_out = ctx.run(ctx.on.install(), state_in)
        # Install should not crash; status depends on charm logic
        assert state_out.unit_status.name in (
            "active",
            "waiting",
            "blocked",
            "maintenance",
        )
