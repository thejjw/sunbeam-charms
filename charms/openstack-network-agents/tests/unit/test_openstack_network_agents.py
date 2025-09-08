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
    def test_on_install_sets_status_and_installs_snap(self, mock_run):
        """Test on install event installs snap and sets ActiveStatus."""
        self.harness.charm.on.install.emit()
        mock_run.assert_called_once_with(
            ["snap", "install", charm.SNAP],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            self.harness.charm.unit.status,
            ActiveStatus("installed"),
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
        # Should call snap set and snap run (now 4 calls expected)
        self.assertEqual(mock_run.call_count, 4)
        snap_set_call = mock_run.call_args_list[0]
        snap_run_call = mock_run.call_args_list[1]
        snap_set_call_2 = mock_run.call_args_list[2]
        snap_run_call_2 = mock_run.call_args_list[3]
        self.assertIn("snap set", " ".join(snap_set_call[0][0]))
        self.assertIn("snap run", " ".join(snap_run_call[0][0]))
        self.assertIn("snap set", " ".join(snap_set_call_2[0][0]))
        self.assertIn("snap run", " ".join(snap_run_call_2[0][0]))

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
