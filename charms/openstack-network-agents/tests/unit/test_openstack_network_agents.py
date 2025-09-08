#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Define openstack-network-agents tests."""

import unittest
from unittest.mock import (
    call,
    patch,
)

import charm
from ops.model import (
    ActiveStatus,
    BlockedStatus,
)
from ops.testing import (
    Harness,
)


class TestOpenstackNetworkAgentsCharm(unittest.TestCase):
    """Unit tests for Openstack Network Agents charm."""

    def setUp(self):
        """Run test setup."""
        self.harness = Harness(charm.OpenstackNetworkAgentsCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("charm.subprocess.run")
    def test_on_install_installs_and_connects(self, mock_run):
        """Install should install the snap and connect required plugs."""
        self.harness.charm.on.install.emit()

        expected = [
            call(
                ["snap", "install", charm.SNAP],
                check=True,
                capture_output=True,
                text=True,
            ),
            call(
                [
                    "snap",
                    "connect",
                    f"{charm.SNAP}:network-control",
                    ":network-control",
                ],
                check=True,
                capture_output=True,
                text=True,
            ),
            call(
                ["snap", "connect", f"{charm.SNAP}:network", ":network"],
                check=True,
                capture_output=True,
                text=True,
            ),
            call(
                [
                    "snap",
                    "connect",
                    f"{charm.SNAP}:ovn-chassis",
                    "microovn:ovn-chassis",
                ],
                check=True,
                capture_output=True,
                text=True,
            ),
        ]
        self.assertEqual(mock_run.call_args_list, expected)

        self.assertEqual(
            self.harness.charm.unit.status, ActiveStatus("installed")
        )

    @patch("charm.subprocess.run")
    def test_on_changed_missing_config_sets_blocked(self, mock_run):
        """Test on config changed with missing config sets BlockedStatus."""
        self.harness.update_config(
            {
                "external-interface": "",
                "bridge-name": "",
                "physnet-name": "",
                "enable-chassis-as-gw": False,
            }
        )
        self.harness.charm.on.config_changed.emit()
        self.assertIsInstance(self.harness.charm.unit.status, BlockedStatus)
        self.assertIn(
            "missing: external-interface, bridge-name, physnet-name",
            str(self.harness.charm.unit.status),
        )
        mock_run.assert_not_called()

    @patch("charm.subprocess.run")
    def test_on_changed_valid_config_applies_mapping(self, mock_run):
        """Test on config changed with valid config applies bridge mapping."""
        self.harness.update_config(
            {
                "external-interface": "eth0",
                "bridge-name": "br-ex",
                "physnet-name": "physnet1",
                "enable-chassis-as-gw": True,
            }
        )
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.charm.unit.status,
            ActiveStatus("ovs bridge mapping configured"),
        )
        self.assertEqual(mock_run.call_count, 4)
        self.assertEqual(
            mock_run.call_args_list[0],
            call(
                [
                    "snap",
                    "set",
                    charm.SNAP,
                    "network.interface=eth0",
                    "network.bridge=br-ex",
                    "network.physnet=physnet1",
                    "network.enable-chassis-as-gw=true",
                ],
                check=True,
                capture_output=True,
                text=True,
            ),
        )
        self.assertEqual(
            mock_run.call_args_list[1],
            call(
                [
                    "snap",
                    "run",
                    f"{charm.SNAP}.bridge-setup",
                    "apply-from-snap-config",
                ],
                check=True,
                capture_output=True,
                text=True,
            ),
        )

    @patch("charm.subprocess.run", side_effect=Exception("snap error"))
    def test_on_install_snap_error(self, mock_run):
        """Test on install event with snap error raises exception."""
        with self.assertRaises(Exception):
            self.harness.charm.on.install.emit()

    @patch("charm.subprocess.run", side_effect=Exception("snap error"))
    def test_on_changed_snap_error(self, mock_run):
        """Test on config changed event with snap error raises exception."""
        with self.assertRaises(Exception):
            self.harness.update_config(
                {
                    "external-interface": "eth0",
                    "bridge-name": "br-ex",
                    "physnet-name": "physnet1",
                    "enable-chassis-as-gw": True,
                }
            )
            self.harness.charm.on.config_changed.emit()
