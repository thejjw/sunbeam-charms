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

import dataclasses
from unittest.mock import (
    patch,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_contains,
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
        assert_config_file_contains(
            state_out,
            ctx,
            "designate-bind",
            "/etc/bind/named.conf",
            ['include "/etc/bind/named.conf.root-hints";'],
        )

        config_path = assert_config_file_exists(
            state_out, ctx, "designate-bind", "/etc/bind/named.conf"
        )
        assert "named.conf.default-zones" not in config_path.read_text()

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


class TestForwarders:
    """Forwarders and recursion rendering in named.conf.options."""

    OPTIONS_FILE = "/etc/bind/named.conf.options"

    @staticmethod
    def _forwarders_block(forwarders: list[str]) -> str:
        """Expected rendered forwarders block."""
        entries = "".join(f"                {f};\n" for f in forwarders)
        return (
            "        forwarders {\n" + entries + "        };\n"
            "        forward only;\n"
        )

    @staticmethod
    def _recursion_block(nets: list[str]) -> str:
        """Expected rendered recursion block."""
        entries = "".join(f"                {n};\n" for n in nets)
        return (
            "        recursion yes;\n"
            "        allow-recursion {\n" + entries + "        };\n"
        )

    def _run(self, ctx, state, config) -> testing.State:
        """Run config-changed with the given charm config."""
        state = dataclasses.replace(state, config=config)
        return ctx.run(ctx.on.config_changed(), state)

    def _render_options(self, ctx, state, config) -> str:
        """Run config-changed and return rendered named.conf.options."""
        state_out = self._run(ctx, state, config)
        assert state_out.unit_status == testing.ActiveStatus("")
        config_path = assert_config_file_exists(
            state_out, ctx, "designate-bind", self.OPTIONS_FILE
        )
        return config_path.read_text()

    @staticmethod
    def _assert_no_forwarding_or_recursion(content: str) -> None:
        """Assert neither forwarding nor recursion is configured."""
        assert "forwarders {" not in content
        assert "forward only;" not in content
        assert "recursion yes;" not in content
        assert "allow-recursion" not in content

    @staticmethod
    def _options_block(content: str) -> str:
        """Return the body of the top-level options block."""
        start = content.index("options {")
        end = content.index("\n};", start)
        return content[start:end]

    def test_no_options(self, ctx, complete_state):
        """No forwarders or recursion nets → configuration unchanged."""
        content = self._render_options(ctx, complete_state, {})
        self._assert_no_forwarding_or_recursion(content)
        assert "Forwarders are managed" not in content

    def test_empty_entries_treated_as_unset(self, ctx, complete_state):
        """Values with only separators/whitespace are treated as unset."""
        content = self._render_options(
            ctx,
            complete_state,
            {"forwarders": " ; ; ", "allowed_recursion_nets": ";"},
        )
        self._assert_no_forwarding_or_recursion(content)

    def test_recursion_nets_without_forwarders(self, ctx, complete_state):
        """allowed_recursion_nets alone enables restricted recursion."""
        content = self._render_options(
            ctx,
            complete_state,
            {"allowed_recursion_nets": "10.0.0.0/8; 192.168.0.0/16;"},
        )
        assert "forwarders {" not in content
        assert "forward only;" not in content
        assert (
            self._recursion_block(["10.0.0.0/8", "192.168.0.0/16"])
            in self._options_block(content) + "\n"
        )

    def test_forwarders_with_recursion_nets(self, ctx, complete_state):
        """Forwarders are validated, normalised and rendered with an ACL."""
        content = self._render_options(
            ctx,
            complete_state,
            {
                "forwarders": " 8.8.8.8 ; ;2001:4860:4860::8888; ",
                "allowed_recursion_nets": "10.1.2.3/8",
            },
        )
        options = self._options_block(content) + "\n"
        assert (
            self._forwarders_block(["8.8.8.8", "2001:4860:4860::8888"])
            in options
        )
        assert self._recursion_block(["10.0.0.0/8"]) in options
        assert "any;" not in content.split("allow-recursion", 1)[1]

    def test_forwarders_without_recursion_nets_blocks(
        self, ctx, complete_state
    ):
        """Forwarders without allowed_recursion_nets → blocked, no any."""
        state_out = self._run(ctx, complete_state, {"forwarders": "8.8.8.8"})
        assert state_out.unit_status == testing.BlockedStatus(
            "(workload) allowed_recursion_nets must be set when forwarders is"
            " set"
        )

    @pytest.mark.parametrize(
        "config,bad_value",
        [
            (
                {
                    "forwarders": "8.8.8.8\n        }",
                    "allowed_recursion_nets": "10.0.0.0/8",
                },
                "8.8.8.8\n",
            ),
            (
                {
                    "forwarders": "dns.google",
                    "allowed_recursion_nets": "10.0.0.0/8",
                },
                "dns.google",
            ),
            (
                {"forwarders": "any", "allowed_recursion_nets": "10.0.0.0/8"},
                "forwarders {",
            ),
            ({"allowed_recursion_nets": "10.0.0.0/33"}, "10.0.0.0/33"),
            ({"allowed_recursion_nets": "any"}, "allow-recursion"),
            (
                {
                    "allowed_recursion_nets": "10.0.0.0/8 }; allow-transfer { any"
                },
                "allow-transfer",
            ),
        ],
    )
    def test_invalid_config_blocks(
        self, ctx, complete_state, config, bad_value
    ):
        """Invalid entries → blocked and never rendered."""
        state_out = self._run(ctx, complete_state, config)
        assert state_out.unit_status.name == "blocked"
        assert "Invalid" in state_out.unit_status.message

        container = state_out.get_container("designate-bind")
        options_file = container.get_filesystem(
            ctx
        ) / self.OPTIONS_FILE.lstrip("/")
        if options_file.exists():
            assert bad_value not in options_file.read_text()


class TestConfigParsing:
    """Validation helpers for forwarders and allowed_recursion_nets."""

    def test_parse_forwarders(self):
        """IPv4/IPv6 forwarders are stripped; empty entries dropped."""
        assert charm.parse_forwarders(" 8.8.8.8 ;;2001:4860:4860::8888;") == [
            "8.8.8.8",
            "2001:4860:4860::8888",
        ]
        assert charm.parse_forwarders("") == []
        assert charm.parse_forwarders(None) == []

    @pytest.mark.parametrize(
        "value", ["dns.google", "any", "8.8.8.8/32", "8.8.8.8 port 53"]
    )
    def test_parse_forwarders_invalid(self, value):
        """Non IP address forwarders are rejected."""
        with pytest.raises(ValueError, match="Invalid forwarders entry"):
            charm.parse_forwarders(value)

    def test_parse_recursion_nets(self):
        """Networks are normalised; single addresses become host networks."""
        assert charm.parse_recursion_nets(
            "10.1.2.3/8; 192.168.1.10;2001:db8::/32"
        ) == ["10.0.0.0/8", "192.168.1.10/32", "2001:db8::/32"]
        assert charm.parse_recursion_nets(" ; ") == []

    @pytest.mark.parametrize(
        "value", ["any", "localnets", "10.0.0.0/33", "10.0.0.0/8 }"]
    )
    def test_parse_recursion_nets_invalid(self, value):
        """Non CIDR entries, including BIND ACL keywords, are rejected."""
        with pytest.raises(
            ValueError, match="Invalid allowed_recursion_nets entry"
        ):
            charm.parse_recursion_nets(value)


class TestPebbleReady:
    """Pebble-ready event with peers → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the bind service."""
        container = complete_state.get_container("designate-bind")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("designate-bind")
        assert "dns-server" in out_container.layers
        layer = out_container.layers["dns-server"]
        service = layer.to_dict()["services"]["dns-server"]
        assert service["command"] == "/usr/sbin/named -g -u bind"
        assert "user" not in service
        assert "group" not in service

        plan = out_container.plan.to_dict()
        assert list(plan["services"]) == ["dns-server"]

        assert out_container.service_statuses.get("dns-server") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_prepares_restart_safe_state(
        self, ctx, complete_state
    ):
        """First start prepares dynamic state once and preserves it later."""
        container = complete_state.get_container("designate-bind")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        out_container = state_out.get_container("designate-bind")
        filesystem = out_container.get_filesystem(ctx)
        assert (filesystem / "run/named").is_dir()
        assert (filesystem / "var/cache/bind").is_dir()
        assert (filesystem / "run/designate-bind-charm-prepared").is_file()

        dynamic_zones = filesystem / "var/cache/bind/_default.nzd"
        assert dynamic_zones.read_text() == "root-owned"
        assert dynamic_zones.stat().st_mode & 0o777 == 0o660
        dynamic_zones.write_text("preserved")
        state_out = ctx.run(ctx.on.config_changed(), state_out)
        assert dynamic_zones.read_text() == "preserved"

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


class TestPerUnitHostPublication:
    """Headless hostname publication on dns-backend relation."""

    FAKE_FQDN = "bind-0.bind-endpoints.openstack.svc.cluster.local"

    def test_leader_publishes_unit_host(
        self, ctx, complete_state_with_dns_backend
    ):
        """Leader unit publishes headless hostname."""
        with patch("socket.getfqdn", return_value=self.FAKE_FQDN):
            state_out = ctx.run(
                ctx.on.config_changed(),
                complete_state_with_dns_backend,
            )

        relation = next(
            r for r in state_out.relations if r.endpoint == "dns-backend"
        )

        assert relation.local_unit_data["host"] == self.FAKE_FQDN

    def test_non_leader_publishes_unit_host(
        self, ctx, non_leader_state_with_dns_backend
    ):
        """Non-leader unit publishes headless hostname."""
        relation = next(
            r
            for r in non_leader_state_with_dns_backend.relations
            if r.endpoint == "dns-backend"
        )

        with patch("socket.getfqdn", return_value=self.FAKE_FQDN):
            state_out = ctx.run(
                ctx.on.relation_changed(relation),
                non_leader_state_with_dns_backend,
            )

        out_relation = next(
            r for r in state_out.relations if r.endpoint == "dns-backend"
        )

        assert out_relation.local_unit_data["host"] == self.FAKE_FQDN
