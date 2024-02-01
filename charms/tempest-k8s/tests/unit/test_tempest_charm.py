#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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

"""Unit tests for Tempest operator."""

import json
import pathlib

import charm
import mock
import ops_sunbeam.test_utils as test_utils
import yaml
from utils.constants import (
    CONTAINER,
    TEMPEST_HOME,
    TEMPEST_READY_KEY,
)

TEST_TEMPEST_ENV = {
    "OS_REGION_NAME": "RegionOne",
    "OS_IDENTITY_API_VERSION": "3",
    "OS_AUTH_VERSION": "3",
    "OS_AUTH_URL": "http://10.6.0.23/openstack-keystone/v3",
    "OS_USERNAME": "tempest",
    "OS_PASSWORD": "password",
    "OS_USER_DOMAIN_NAME": "tempest",
    "OS_PROJECT_NAME": "tempest-CloudValidation",
    "OS_PROJECT_DOMAIN_NAME": "tempest",
    "OS_DOMAIN_NAME": "tempest",
    "TEMPEST_CONCURRENCY": "4",
    "TEMPEST_CONF": "/var/lib/tempest/workspace/etc/tempest.conf",
    "TEMPEST_HOME": "/var/lib/tempest",
    "TEMPEST_LIST_DIR": "/tempest_test_lists",
    "TEMPEST_OUTPUT": "/var/lib/tempest/workspace/tempest-output.log",
    "TEMPEST_TEST_ACCOUNTS": "/var/lib/tempest/workspace/test_accounts.yaml",
    "TEMPEST_WORKSPACE": "tempest",
    "TEMPEST_WORKSPACE_PATH": "/var/lib/tempest/workspace",
}


charmcraft = (
    pathlib.Path(__file__).parents[2] / "charmcraft.yaml"
).read_text()
config = yaml.dump(yaml.safe_load(charmcraft)["config"])
actions = yaml.dump(yaml.safe_load(charmcraft)["actions"])


class _TempestTestOperatorCharm(charm.TempestOperatorCharm):
    """Test Operator Charm for Tempest operator."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)


class TestTempestOperatorCharm(test_utils.CharmTestCase):
    """Classes for testing tempest charms."""

    PATCHES = []

    def setUp(self):
        """Setup Placement tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _TempestTestOperatorCharm,
            container_calls=self.container_calls,
            charm_metadata=charmcraft,
            charm_config=config,
            charm_actions=actions,
        )

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader()

    def add_identity_ops_relation(self, harness):
        """Add identity resource relation."""
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.user_id_ops.callback_f = mock.Mock()
        harness.charm.user_id_ops.get_user_credential = mock.Mock(
            return_value={
                "username": "tempest",
                "password": "password",
                "domain-name": "tempest",
                "project-name": "tempest-CloudValidation",
                "auth-url": "http://10.6.0.23/openstack-keystone/v3",
            },
        )

        # Only show the list_endpoint ops for simplicity
        harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "response": json.dumps(
                    {
                        "id": "c8e02ce67f57057d1a0d6660c6571361eea1a03d749d021d33e13ea4b0a7982a",
                        "tag": "setup_tempest_resource",
                        "ops": [
                            {
                                "name": "some_other_ops",
                                "return-code": 0,
                                "value": "",
                            },
                            {
                                "name": "list_endpoint",
                                "return-code": 0,
                                "value": [
                                    {
                                        "id": "68c4eba8b01f41829d30cf2519998883",
                                        "service_id": "b2a08eea7699460e838f7cce97529e55",
                                        "interface": "admin",
                                        "region": "RegionOne",
                                        "url": "http://10.152.183.48:5000/v3",
                                        "enabled": True,
                                    }
                                ],
                            },
                        ],
                    }
                )
            },
        )
        return rel_id

    def add_logging_relation(self, harness):
        """Add logging relation."""
        rel_id = harness.add_relation("logging", "loki")
        harness.add_relation_unit(rel_id, "loki/0")
        harness.charm.loki.interface = mock.Mock()
        return rel_id

    def add_grafana_dashboard_relation(self, harness):
        """Add grafana dashboard relation."""
        rel_id = harness.add_relation("grafana_dashboard", "grafana")
        harness.add_relation_unit(rel_id, "grafana/0")
        harness.charm.grafana.interface = mock.Mock()
        return rel_id

    def test_pebble_ready_handler(self):
        """Test Pebble ready event is captured."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all integrations ready and okay for operator."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        self.harness.charm.is_tempest_ready = mock.Mock(return_value=True)

        self.harness.update_config({"schedule": "0 0 */7 * *"})

        config_files = [
            "/etc/crontab",
            "/usr/local/sbin/tempest-run-wrapper",
            "/usr/local/sbin/tempest-init",
        ]
        for f in config_files:
            self.check_file(charm.CONTAINER, f)

        self.assertEqual(self.harness.charm.status.message(), "")
        self.assertEqual(self.harness.charm.status.status.name, "active")

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_config_context_schedule(self):
        """Test config context contains the schedule as expected."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        # schedule is disabled if it's not ready, so set it ready for testing
        self.harness.charm.is_tempest_ready = mock.Mock(return_value=True)

        # ok schedule
        schedule = "0 0 */7 * *"
        self.harness.update_config({"schedule": schedule})
        self.assertEqual(
            self.harness.charm.contexts().tempest.schedule, schedule
        )

        # too frequent
        schedule = "* * * * *"
        self.harness.update_config({"schedule": schedule})
        self.assertEqual(self.harness.charm.contexts().tempest.schedule, "")

        # disabled
        schedule = ""
        self.harness.update_config({"schedule": schedule})
        self.assertEqual(self.harness.charm.contexts().tempest.schedule, "")

        # tempest init not ready
        self.harness.charm.is_tempest_ready = mock.Mock(return_value=False)
        schedule = "0 0 */7 * *"
        self.harness.update_config({"schedule": schedule})
        self.assertEqual(self.harness.charm.contexts().tempest.schedule, "")

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_validate_action_invalid_regex(self):
        """Test validate action with invalid regex provided."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        action_event = mock.Mock()
        action_event.params = {
            "serial": False,
            "regex": "test(",
            "exclude-regex": "",
            "test-list": "",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_called_with(
            "'test(' is an invalid regex: missing ), unterminated subpattern at position 4"
        )

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_validate_action_invalid_list(self):
        """Test validate action with invalid list provided."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        file1 = mock.Mock()
        file1.name = "file_1"
        file2 = mock.Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = mock.Mock(
            return_value=[file1, file2]
        )

        action_event = mock.Mock()
        action_event.params = {
            "serial": False,
            "regex": "",
            "exclude-regex": "",
            "test-list": "nonexistent",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_called_with(
            "'nonexistent' is not a known test list. Please run list-tests action to view available lists."
        )

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_validate_action_success(self):
        """Test validate action with default params."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        file1 = mock.Mock()
        file1.name = "file_1"
        file2 = mock.Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = mock.Mock(
            return_value=[file1, file2]
        )
        exec_mock = mock.Mock()
        self.harness.charm.pebble_handler().execute = exec_mock

        action_event = mock.Mock()
        action_event.params = {
            "serial": False,
            "regex": "",
            "exclude-regex": "",
            "test-list": "",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_not_called()
        exec_mock.assert_called_with(
            ["tempest-run-wrapper", "--parallel"],
            user="tempest",
            group="tempest",
            working_dir=TEMPEST_HOME,
            exception_on_error=True,
            environment=TEST_TEMPEST_ENV,
        )

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_validate_action_params(self):
        """Test validate action with more params."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        file1 = mock.Mock()
        file1.name = "file_1"
        file2 = mock.Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = mock.Mock(
            return_value=[file1, file2]
        )
        exec_mock = mock.Mock()
        self.harness.charm.pebble_handler().execute = exec_mock

        action_event = mock.Mock()
        action_event.params = {
            "serial": True,
            "regex": "re1 re2",
            "exclude-regex": "excludethis",
            "test-list": "file_1",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_not_called()
        exec_mock.assert_called_with(
            [
                "tempest-run-wrapper",
                "--serial",
                "--regex",
                "re1 re2",
                "--exclude-regex",
                "excludethis",
                "--load-list",
                "/tempest_test_lists/file_1",
            ],
            user="tempest",
            group="tempest",
            working_dir=TEMPEST_HOME,
            exception_on_error=True,
            environment=TEST_TEMPEST_ENV,
        )

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_get_list_action(self):
        """Test get-list action."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        file1 = mock.Mock()
        file1.name = "file_1"
        file2 = mock.Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = mock.Mock(
            return_value=[file1, file2]
        )

        action_event = mock.Mock()
        self.harness.charm._on_get_lists_action(action_event)
        action_event.fail.assert_not_called()

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_get_list_action_not_ready(self):
        """Test get-list action when pebble is not ready."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        file1 = mock.Mock()
        file1.name = "file_1"
        file2 = mock.Mock()
        file2.name = "file_2"
        self.harness.charm.unit.get_container(CONTAINER).can_connect = (
            mock.Mock(return_value=False)
        )

        action_event = mock.Mock()
        self.harness.charm._on_get_lists_action(action_event)
        action_event.fail.assert_called_with("pebble is not ready")

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_blocked_status_invalid_schedule(self):
        """Test to verify blocked status with invalid schedule config."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        self.harness.charm.is_tempest_ready = mock.Mock(return_value=True)

        # invalid schedule should make charm in blocked status
        self.harness.update_config({"schedule": "* *"})
        self.assertIn("invalid schedule", self.harness.charm.status.message())
        self.assertEqual(self.harness.charm.status.status.name, "blocked")

        # updating the schedule to something valid should unblock it
        self.harness.update_config({"schedule": "*/20 * * * *"})
        self.assertEqual(self.harness.charm.status.message(), "")
        self.assertEqual(self.harness.charm.status.status.name, "active")

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_error_initing_tempest(self):
        """Test to verify blocked status if tempest init fails."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        mock_pebble = mock.Mock()
        mock_pebble.init_tempest = mock.Mock(side_effect=RuntimeError)
        self.harness.charm.pebble_handler = mock.Mock(return_value=mock_pebble)

        self.harness.update_config({"schedule": "*/21 * * * *"})

        self.assertIn(
            "tempest init failed", self.harness.charm.status.message()
        )
        self.assertEqual(self.harness.charm.status.status.name, "blocked")

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_is_tempest_ready(self):
        """Test the tempest ready check method."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        self.harness.charm.leader_get = mock.Mock(return_value="true")
        self.assertTrue(self.harness.charm.is_tempest_ready())

        self.harness.charm.leader_get = mock.Mock(return_value="")
        self.assertFalse(self.harness.charm.is_tempest_ready())

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_set_tempest_ready(self):
        """Test the tempest ready set method."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        self.harness.charm.leader_set = mock.Mock()
        self.harness.charm.set_tempest_ready(True)
        self.harness.charm.leader_set.assert_called_with(
            {TEMPEST_READY_KEY: "true"}
        )

        self.harness.charm.leader_set = mock.Mock()
        self.harness.charm.set_tempest_ready(False)
        self.harness.charm.leader_set.assert_called_with(
            {TEMPEST_READY_KEY: ""}
        )

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_init_tempest_method(self):
        """Test the tempest init method logic."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        # tempest init not run yet, pebble init tempest fails
        pebble_mock = mock.Mock()
        pebble_mock.init_tempest = mock.Mock(side_effect=RuntimeError)
        self.harness.charm.pebble_handler = mock.Mock(return_value=pebble_mock)
        self.harness.charm.is_tempest_ready = mock.Mock(return_value=False)
        self.harness.charm.set_tempest_ready = mock.Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

        # tempest init succeeds
        pebble_mock.init_tempest.side_effect = None
        self.harness.charm.set_tempest_ready = mock.Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_called_once_with(True)

        # tempest init already run
        self.harness.charm.is_tempest_ready = mock.Mock(return_value=True)
        self.harness.charm.set_tempest_ready = mock.Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_not_called()

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)

    def test_upgrade_charm(self):
        """Test upgrade charm updates things as required."""
        test_utils.set_all_pebbles_ready(self.harness)
        logging_rel_id = self.add_logging_relation(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)
        grafana_dashboard_rel_id = self.add_grafana_dashboard_relation(
            self.harness
        )

        self.harness.charm.set_tempest_ready = mock.Mock()

        self.harness.charm._on_upgrade_charm(mock.Mock())

        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

        self.harness.remove_relation(logging_rel_id)
        self.harness.remove_relation(identity_ops_rel_id)
        self.harness.remove_relation(grafana_dashboard_rel_id)
