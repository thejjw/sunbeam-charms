# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import json
from secrets import choice
import subprocess

import requests
import zaza
import zaza.model as model
import zaza.openstack.charm_tests.test_utils as test_utils


class ClusterdTest(test_utils.BaseCharmTest):
    """Charm tests for clusterd."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(ClusterdTest, cls).setUpClass(
            application_name="sunbeam-clusterd"
        )

    def test_100_connect_to_clusterd(self):
        """Try sending data to an endpoint."""
        action = model.run_action_on_leader(
            self.application_name, "get-credentials"
        )
        url = action.data["results"]["url"] + "/1.0/config/100_connect"
        response = requests.put(url, json={"data": "test"}, verify=False)
        response.raise_for_status()
        response = requests.get(url, verify=False)
        response.raise_for_status()
        self.assertEqual(
            json.loads(response.json()["metadata"])["data"], "test"
        )

    def test_200_scale_up(self):
        """Scale up."""
        model.add_unit(self.application_name, count=2)
        model.block_until_unit_count(self.application_name, 3)
        model.block_until_all_units_idle()
        units = [unit.name for unit in model.get_units(self.application_name)]
        for unit in units:
            model.block_until_unit_wl_status(unit, "active", timeout=30)

    def test_201_scale_down(self):
        """Scale down."""
        units = [unit.name for unit in model.get_units(self.application_name)]
        model.destroy_unit(self.application_name, choice(units))
        model.block_until_unit_count(self.application_name, 2)
        model.block_until_all_units_idle()

        units = [unit.name for unit in model.get_units(self.application_name)]
        for unit in units:
            model.block_until_unit_wl_status(unit, "active", timeout=30)

    def _query_clusterd(self, unit: str, method: str, path: str):
        cmd = [
            "juju",
            "ssh",
            unit,
            "sudo",
            "curl",
            "-s",
            "--unix-socket",
            "/var/snap/openstack/common/state/control.socket",
            "-X",
            method,
            "http://localhost" + path,
        ]
        try:
            stdout = subprocess.check_output(cmd)
        except subprocess.CalledProcessError:
            logging.exception("Failed to query clusterd on %s", unit)
            self.fail("Failed to query clusterd on {}".format(unit))
        return json.loads(stdout.decode("utf-8"))

    def test_202_scale_down_voter(self):
        """Scale down the voter member.

        When there's only 2 members left, 1 is voter, and 1 is spare.
        There has been issues when the voter member is removed.
        """
        units = [unit.name for unit in model.get_units(self.application_name)]
        output = self._query_clusterd(units[0], "GET", "/cluster/1.0/cluster")
        for member in output["metadata"]:
            if member["role"] == "voter":
                voter = member["name"]
                break
        else:
            self.fail("No voter found")
        for unit in units:
            if unit.replace("/", "-") == voter:
                model.destroy_unit(self.application_name, unit)
                units.remove(unit)
                break
        else:
            self.fail("No unit found for voter {}".format(voter))
        model.block_until_unit_count(self.application_name, 1)
        model.block_until_all_units_idle()
        model.block_until_unit_wl_status(units[0], "active", timeout=60)
        output = self._query_clusterd(units[0], "GET", "/cluster/1.0/cluster")
        self.assertEqual(output["status_code"], 200)
        self.assertEqual(len(output["metadata"]), 1)
