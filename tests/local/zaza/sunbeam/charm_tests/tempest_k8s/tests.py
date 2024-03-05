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

# zaza wait for application state checks all apps,
# so this is for the normal expected state for other apps (not tempest-k8s).
# Individual tests can reuse this, merging with the expected state for tempest-k8s at the time.
OTHER_APP_EXPECTED_STATES = {
    "traefik": {
        "workload-status-message-regex": "^$",
    },
    "mysql": {
        "workload-status-message-regex": "^.*$",
    },
    "tls-operator": {
        "workload-status-message-regex": "^$",
    },
    "rabbitmq": {
        "workload-status-message-regex": "^$",
    },
    "ovn-central": {
        "workload-status-message-regex": "^$",
    },
    "ovn-relay": {
        "workload-status-message-regex": "^$",
    },
    "keystone": {
        "workload-status-message-regex": "^$",
    },
    "glance": {
        "workload-status-message-regex": "^$",
    },
    "nova": {
        "workload-status-message-regex": "^$",
    },
    "placement": {
        "workload-status-message-regex": "^$",
    },
    "neutron": {
        "workload-status-message-regex": "^$",
    },
}


class TempestK8sTest(test_utils.BaseCharmTest):
    """Charm tests for tempest-k8s."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(TempestK8sTest, cls).setUpClass(application_name="tempest")

    def test_get_lists(self):
        """Verify that the get-lists action returns list names as expected."""
        action = model.run_action_on_leader(self.application_name, "get-lists")
        lists = action.data["results"]["stdout"].splitlines()
        self.assertIn("readonly-quick", lists)
        self.assertIn("refstack-2022.11", lists)

    def test_bounce_keystone_relation(self):
        """Test removing and re-adding the keystone relation."""
        # verify that the application is blocked when keystone is missing
        model.remove_relation("tempest", "identity-ops", "keystone")
        model.wait_for_application_states(
            states={
                **OTHER_APP_EXPECTED_STATES,
                "tempest": {
                    "workload-status": "blocked",
                    "workload-status-message-regex": r"^\(identity-ops\) integration missing$",
                },
            }
        )

        # And then verify that adding it back
        # results in reaching active/idle state again.
        # ie. successful tempest init again.
        model.add_relation("tempest", "identity-ops", "keystone")
        model.wait_for_application_states(
            states={
                **OTHER_APP_EXPECTED_STATES,
                "tempest": {
                    "workload-status": "active",
                    "workload-status-message-regex": "^$",
                },
            }
        )
