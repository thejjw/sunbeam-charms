# Copyright 2023 Canonical Ltd.
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

"""Tests for Openstack hypervisor utils."""

import unittest
from unittest import (
    mock,
)

import utils


class TestOpenstackHypervisorUtils(unittest.TestCase):
    """Tests for Openstack hypervisor utils."""

    @mock.patch("subprocess.run")
    def test_get_systemd_unit_status_not_found(self, mock_run):
        """Test retrieving inexistent systemd unit status."""
        mock_run.return_value.stdout = "[]"

        self.assertIsNone(utils.get_systemd_unit_status("test.service"))

    @mock.patch("subprocess.run")
    def test_get_systemd_unit_status(self, mock_run):
        """Test retrieving systemd unit status."""
        mock_run.return_value.stdout = (
            '[{"unit":"ovs-vswitchd.service","load":"masked",'
            '"active":"inactive","sub":"dead",'
            '"description":"ovs-vswitchd.service"}]'
        )

        exp_out = {
            "name": "ovs-vswitchd.service",
            "load_state": "masked",
            "active_state": "inactive",
            "substate": "dead",
            "description": "ovs-vswitchd.service",
        }
        out = utils.get_systemd_unit_status("ovs-vswitchd.service")

        self.assertEqual(exp_out, out)
        mock_run.assert_called_once_with(
            [
                "systemctl",
                "list-units",
                "--all",
                "-o",
                "json",
                "--no-pager",
                "ovs-vswitchd.service",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
