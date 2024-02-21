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

from zaza import model
from zaza.openstack.charm_tests import test_utils


class TempestK8sTest(test_utils.BaseCharmTest):
    """Charm tests for tempest-k8s."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(TempestK8sTest, cls).setUpClass(
            application_name="tempest"
        )

    def test_get_lists(self):
        """Verify that the get-lists action returns list names as expected."""
        action = model.run_action_on_leader(
            self.application_name, "get-lists"
        )
        lists = action.data["results"]["stdout"].splitlines()
        self.assertIn("readonly-quick", lists)
        self.assertIn("refstack-2022.11", lists)
