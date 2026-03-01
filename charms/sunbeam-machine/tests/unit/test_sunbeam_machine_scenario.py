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

"""ops.testing (state-transition) tests for sunbeam-machine.

sunbeam-machine is a simple machine charm with no mandatory relations
(only provides sunbeam-machine and optionally requires tracing).
It configures sysctl, installs packages, and manages /etc/environment
proxy settings.
"""

from unittest.mock import (
    mock_open,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
)

# ---------------------------------------------------------------------------
# Tests: install event
# ---------------------------------------------------------------------------


class TestInstallEvent:
    """Install event should run without errors."""

    def test_install_event_runs(self, ctx):
        """Install event should not crash with mocked externals."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.install(), state_in)
        assert state_out.unit_status.name in (
            "active",
            "blocked",
            "waiting",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: config-changed reaches active
# ---------------------------------------------------------------------------


class TestConfigChanged:
    """Config-changed should reach active status (no mandatory relations)."""

    def test_config_changed_leader_active(self, ctx):
        """Leader with no relations should reach active."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "active")

    def test_config_changed_non_leader_active(self, ctx):
        """Non-leader should also reach active."""
        state_in = testing.State(leader=False)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "active")


# ---------------------------------------------------------------------------
# Tests: start event
# ---------------------------------------------------------------------------


class TestStartEvent:
    """Start event should run cleanly."""

    def test_start_event(self, ctx):
        """Start event should not error."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.start(), state_in)
        assert state_out.unit_status.name in (
            "active",
            "blocked",
            "waiting",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: config options
# ---------------------------------------------------------------------------


class TestConfigOptions:
    """Config changes should be handled without errors."""

    def test_config_changed_with_debug(self, ctx):
        """Changing debug config should not crash."""
        state_in = testing.State(
            leader=True,
            config={"debug": True},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in (
            "active",
            "blocked",
            "waiting",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: initial bootstrap (ported from test_charm.py)
# ---------------------------------------------------------------------------


class TestInitial:
    """Install event reads /etc/environment but does not write when no proxy is set."""

    def test_initial_no_write(self, ctx, monkeypatch):
        """config-changed with no proxy config should not write /etc/environment."""
        mock_file = mock_open(read_data="PATH=FAKEPATH")
        monkeypatch.setattr("builtins.open", mock_file)

        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        mock_file().write.assert_not_called()
        assert_unit_status(state_out, "active")


# ---------------------------------------------------------------------------
# Tests: proxy settings (ported from test_charm.py)
# ---------------------------------------------------------------------------


class TestProxySettings:
    """Proxy config changes should update /etc/environment correctly."""

    @pytest.mark.parametrize(
        "env_content,config,expected_content",
        [
            pytest.param(
                {"PATH": "FAKEPATH"},
                {
                    "http_proxy": "http://proxyserver:3128",
                    "https_proxy": "http://proxyserver:3128",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
                id="set-http-https-proxy",
            ),
            pytest.param(
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
                {
                    "http_proxy": "http://proxyserver:3128",
                    "https_proxy": "http://proxyserver:3128",
                    "no_proxy": "localhost,127.0.0.1",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                id="add-no-proxy",
            ),
            pytest.param(
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                {
                    "http_proxy": "http://proxyserver:3120",
                    "https_proxy": "http://proxyserver:3128",
                    "no_proxy": "localhost,127.0.0.1",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                id="update-http-proxy",
            ),
            pytest.param(
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                {
                    "http_proxy": "http://proxyserver:3120",
                    "https_proxy": "http://proxyserver:3128",
                    "no_proxy": "",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
                id="reset-no-proxy",
            ),
        ],
    )
    def test_proxy_settings(
        self, ctx, monkeypatch, env_content, config, expected_content
    ):
        """Proxy config changes should update /etc/environment."""
        env_file_str = "\n".join(f"{k}={v}" for k, v in env_content.items())
        expected_str = "\n".join(
            f"{k}={v}" for k, v in expected_content.items()
        )

        mock_file = mock_open(read_data=env_file_str)
        monkeypatch.setattr("builtins.open", mock_file)

        state_in = testing.State(leader=True, config=config)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        mock_file().write.assert_called_with(expected_str)
        assert_unit_status(state_out, "active")
