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

"""Plain pytest tests for switch-config processing logic in neutron-k8s."""

from unittest.mock import (
    MagicMock,
    PropertyMock,
)

import charm
import pytest

# ---------------------------------------------------------------------------
# Sample config data (mirrors what the harness tests used)
# ---------------------------------------------------------------------------

_BAREMETAL_SAMPLE_CONFIG = """[%(name)s.example.net]
driver = netconf-openconfig
device_params = name:%(name)s
switch_info = %(name)s
switch_id = 00:53:00:0a:0a:0a
host = %(name)s.example.net
username = user
key_filename = /etc/neutron/sshkeys/%(name)s-sshkey
"""

_GENERIC_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = netmiko_arista_eos
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
key_file = /etc/neutron/sshkeys/%(name)s-key
"""


def _baremetal_cfg(name: str) -> str:
    return _BAREMETAL_SAMPLE_CONFIG % {"name": name}


def _generic_cfg(name: str) -> str:
    return _GENERIC_SAMPLE_CONFIG % {"name": name}


# ---------------------------------------------------------------------------
# BaremetalConfigContext tests
# ---------------------------------------------------------------------------


class TestBaremetalConfigContext:
    """Test BaremetalConfigContext.context() logic."""

    @pytest.fixture()
    def baremetal_ctx(self):
        """Create a BaremetalConfigContext with mocked charm."""
        mock_charm = MagicMock()
        mock_charm.baremetal_config.interface.switch_configs = [
            {
                "conf": _baremetal_cfg("nexus"),
                "nexus-sshkey": "foo",
            },
            {
                "conf": _baremetal_cfg("suxen"),
                "suxen-sshkey": "foo",
            },
        ]
        ctx = charm.BaremetalConfigContext.__new__(
            charm.BaremetalConfigContext
        )
        ctx.charm = mock_charm
        return ctx

    def test_enabled_devices(self, baremetal_ctx):
        """Enabled devices lists all config sections."""
        result = baremetal_ctx.context()
        assert result["enabled_devices"] == (
            "nexus.example.net,suxen.example.net"
        )

    def test_configs_contain_all_entries(self, baremetal_ctx):
        """Each switch config block appears in the configs list."""
        result = baremetal_ctx.context()
        assert len(result["configs"]) == 2
        assert _baremetal_cfg("nexus") in result["configs"]
        assert _baremetal_cfg("suxen") in result["configs"]

    def test_additional_files_ssh_keys(self, baremetal_ctx):
        """SSH key files are mapped correctly."""
        result = baremetal_ctx.context()
        additional = result["additional_files"]
        assert additional["/etc/neutron/sshkeys/nexus-sshkey"] == "foo"
        assert additional["/etc/neutron/sshkeys/suxen-sshkey"] == "foo"


# ---------------------------------------------------------------------------
# GenericConfigContext tests
# ---------------------------------------------------------------------------


class TestGenericConfigContext:
    """Test GenericConfigContext.context() logic."""

    @pytest.fixture()
    def generic_ctx(self):
        """Create a GenericConfigContext with mocked charm."""
        mock_charm = MagicMock()
        mock_charm.generic_config.interface.switch_configs = [
            {
                "conf": _generic_cfg("arista"),
                "arista-key": "foo",
            },
            {
                "conf": _generic_cfg("barista"),
                "barista-key": "foo",
            },
        ]
        ctx = charm.GenericConfigContext.__new__(charm.GenericConfigContext)
        ctx.charm = mock_charm
        return ctx

    def test_configs_contain_all_entries(self, generic_ctx):
        """Each switch config block appears in the configs list."""
        result = generic_ctx.context()
        assert len(result["configs"]) == 2
        assert _generic_cfg("arista") in result["configs"]
        assert _generic_cfg("barista") in result["configs"]

    def test_additional_files_ssh_keys(self, generic_ctx):
        """SSH key files are mapped correctly."""
        result = generic_ctx.context()
        additional = result["additional_files"]
        assert additional["/etc/neutron/sshkeys/arista-key"] == "foo"
        assert additional["/etc/neutron/sshkeys/barista-key"] == "foo"


# ---------------------------------------------------------------------------
# NeutronServerPebbleHandler.get_layer() tests
# ---------------------------------------------------------------------------


class TestNeutronServerPebbleLayer:
    """Test that get_layer() includes switch config files when ready."""

    @staticmethod
    def _make_handler(baremetal_ready: bool, generic_ready: bool):
        """Build a NeutronServerPebbleHandler with mocked charm readiness."""
        handler = charm.NeutronServerPebbleHandler.__new__(
            charm.NeutronServerPebbleHandler
        )
        mock_charm = MagicMock()
        type(mock_charm.baremetal_config).ready = PropertyMock(
            return_value=baremetal_ready
        )
        type(mock_charm.generic_config).ready = PropertyMock(
            return_value=generic_ready
        )
        handler.charm = mock_charm
        return handler

    def test_layer_with_baremetal_only(self):
        """Command includes baremetal config file when baremetal is ready."""
        handler = self._make_handler(baremetal_ready=True, generic_ready=False)
        layer = handler.get_layer()
        cmd = layer["services"]["neutron-server"]["command"]
        assert charm.ML2_BAREMETAL_CONF in cmd
        assert charm.ML2_GENERIC_CONF not in cmd

    def test_layer_with_generic_only(self):
        """Command includes generic config file when generic is ready."""
        handler = self._make_handler(baremetal_ready=False, generic_ready=True)
        layer = handler.get_layer()
        cmd = layer["services"]["neutron-server"]["command"]
        assert charm.ML2_GENERIC_CONF in cmd
        assert charm.ML2_BAREMETAL_CONF not in cmd

    def test_layer_with_both(self):
        """Command includes both config files when both are ready."""
        handler = self._make_handler(baremetal_ready=True, generic_ready=True)
        layer = handler.get_layer()
        cmd = layer["services"]["neutron-server"]["command"]
        assert charm.ML2_BAREMETAL_CONF in cmd
        assert charm.ML2_GENERIC_CONF in cmd

    def test_layer_with_neither(self):
        """Command omits switch config files when neither is ready."""
        handler = self._make_handler(
            baremetal_ready=False, generic_ready=False
        )
        layer = handler.get_layer()
        cmd = layer["services"]["neutron-server"]["command"]
        assert charm.ML2_BAREMETAL_CONF not in cmd
        assert charm.ML2_GENERIC_CONF not in cmd
