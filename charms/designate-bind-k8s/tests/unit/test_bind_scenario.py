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

"""Scenario (ops.testing state-transition) tests for designate-bind-k8s."""

from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_exists,
    k8s_container,
    peer_relation,
)


class TestAllRelations:
    """With peer relation and container the charm reaches active."""

    def test_active_with_peers(self, ctx, complete_state):
        """Config-changed with peers and container → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_file_named_conf(self, ctx, complete_state):
        """Peer relation present → named.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "designate-bind", "/etc/bind/named.conf"
        )

    def test_config_file_named_conf_options(self, ctx, complete_state):
        """Peer relation present → named.conf.options is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "designate-bind", "/etc/bind/named.conf.options"
        )

    def test_opens_dns_and_rndc_ports(self, ctx, complete_state):
        """Charm declares DNS (UDP+TCP 53) and RNDC (TCP 953) ports."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.opened_ports == {
            testing.UDPPort(53),
            testing.TCPPort(53),
            testing.TCPPort(953),
        }


class TestPebbleReady:
    """Pebble-ready event with peers → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the bind service."""
        container = complete_state.get_container("designate-bind")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("designate-bind")
        assert "designate-bind" in out_container.layers
        layer = out_container.layers["designate-bind"]
        assert "designate-bind" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("designate-bind") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_active_without_explicit_peers(self, ctx):
        """Pebble-ready without explicit peer relation still goes active."""
        container = k8s_container("designate-bind")
        state_in = testing.State(leader=True, containers=[container])
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")


class TestBlockedWithoutContainer:
    """Config-changed without connectable container → blocked/waiting."""

    def test_blocked_when_no_container(self, ctx):
        """No connectable container → blocked/waiting status."""
        container = k8s_container("designate-bind", can_connect=False)
        state_in = testing.State(
            leader=True,
            relations=[peer_relation()],
            containers=[container],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
        ), f"Expected blocked/waiting, got {state_out.unit_status}"
