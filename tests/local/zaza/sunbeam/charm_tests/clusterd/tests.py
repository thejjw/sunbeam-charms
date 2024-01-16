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

import json
import requests
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
        model.block_until_all_units_idle()

    def test_201_scale_down(self):
        """Scale down."""
        units = model.get_units(self.application_name)
        model.destroy_unit(
            self.application_name, *[unit.name for unit in units][1:]
        )
        model.block_until_all_units_idle()
