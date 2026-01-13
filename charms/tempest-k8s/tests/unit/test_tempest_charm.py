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
from unittest.mock import (
    MagicMock,
    Mock,
    call,
    patch,
)

import charm
import ops_sunbeam.test_utils as test_utils
import utils
import yaml
from ops_sunbeam.guard import (
    BlockedExceptionError,
)
from utils import (
    overrides,
)
from utils.constants import (
    CONTAINER,
    TEMPEST_ADHOC_OUTPUT,
    TEMPEST_HOME,
    TEMPEST_PERIODIC_OUTPUT,
    TEMPEST_READY_KEY,
    get_tempest_concurrency,
)
from utils.types import (
    TempestEnvVariant,
)

TEST_TEMPEST_ENV = {
    "OS_REGION_NAME": "RegionOne",
    "OS_IDENTITY_API_VERSION": "3",
    "OS_AUTH_VERSION": "3",
    "OS_AUTH_URL": "http://10.6.0.23/openstack-keystone/v3",
    "OS_USERNAME": "tempest",
    "OS_PASSWORD": "password",
    "OS_USER_DOMAIN_NAME": "tempest",
    "OS_PROJECT_NAME": "CloudValidation-tempest",
    "OS_PROJECT_DOMAIN_NAME": "tempest",
    "OS_DOMAIN_NAME": "tempest",
    "OS_PROJECT_DOMAIN_ID": "tempest-domain-id",
    "OS_USER_DOMAIN_ID": "tempest-domain-id",
    "OS_DOMAIN_ID": "tempest-domain-id",
    "TEMPEST_CONCURRENCY": "4",
    "TEMPEST_ACCOUNTS_COUNT": "8",
    "TEMPEST_CONF": "/var/lib/tempest/workspace/etc/tempest.conf",
    "TEMPEST_EXCLUDE_LIST": "/var/lib/tempest/tempest_exclude_list.txt",
    "TEMPEST_HOME": "/var/lib/tempest",
    "TEMPEST_LIST_DIR": "/tempest_test_lists",
    "TEMPEST_OUTPUT": "/var/lib/tempest/workspace/tempest-validation.log",
    "TEMPEST_TEST_ACCOUNTS": "/var/lib/tempest/workspace/test_accounts.yaml",
    "TEMPEST_WORKSPACE": "tempest",
    "TEMPEST_WORKSPACE_PATH": "/var/lib/tempest/workspace",
    "TEMPEST_CONFIG_OVERRIDES": " ".join(
        (
            overrides.get_swift_overrides(),
            overrides.get_compute_overrides(),
            overrides.get_ironic_overrides(),
            overrides.get_manila_overrides(),
        ),
    ),
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

    def setUp(self):
        """Setup Placement tests."""
        super().setUp(charm, [])
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
        self.patch_obj(utils.cleanup, "Connection")
        self.patch_obj(
            utils.cleanup, "_get_exclusion_resources"
        ).return_value = {"projects": set(), "users": set()}

    def add_identity_ops_relation(self, harness):
        """Add identity resource relation."""
        self.harness.charm.set_tempest_ready = Mock()
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.user_id_ops.callback_f = Mock()
        harness.charm.user_id_ops.get_user_credential = Mock(
            return_value={
                "username": "tempest",
                "password": "password",
                "domain-name": "tempest",
                "domain-id": "tempest-domain-id",
                "project-name": "CloudValidation-tempest",
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
        rel_id = test_utils.add_complete_logging_relation(harness)
        harness.charm.logging.interface = Mock()
        harness.charm.logging.interface._promtail_config = Mock()
        return rel_id

    def add_grafana_dashboard_relation(self, harness):
        """Add grafana dashboard relation."""
        rel_id = harness.add_relation("grafana-dashboard", "grafana")
        harness.add_relation_unit(rel_id, "grafana/0")
        harness.charm.grafana.interface = Mock()
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

        self.harness.charm.is_tempest_ready = Mock(return_value=True)

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
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        # schedule is disabled if it's not ready, so set it ready for testing
        self.harness.charm.is_tempest_ready = Mock(return_value=True)

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
        self.harness.charm.is_tempest_ready = Mock(return_value=False)
        self.harness.charm.peers = Mock()
        schedule = "0 0 */7 * *"
        self.harness.update_config({"schedule": schedule})
        self.assertEqual(self.harness.charm.contexts().tempest.schedule, "")

    def test_validate_action_invalid_regex(self):
        """Test validate action with invalid regex provided."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        action_event = Mock()
        action_event.params = {
            "serial": False,
            "regex": "test(",
            "exclude-regex": "",
            "test-list": "",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_called_once()
        self.assertEqual(
            "'test(' is an invalid regex: missing ), unterminated subpattern at position 4",
            action_event.set_results.call_args.args[0]["error"],
        )

    def test_validate_action_invalid_list(self):
        """Test validate action with invalid list provided."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        file1 = Mock()
        file1.name = "file_1"
        file2 = Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = Mock(
            return_value=[file1, file2]
        )

        action_event = Mock()
        action_event.params = {
            "serial": False,
            "regex": "",
            "exclude-regex": "",
            "test-list": "nonexistent",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_called_once()
        self.assertEqual(
            "'nonexistent' is not a known test list. Please run get-lists action to view available lists.",
            action_event.set_results.call_args.args[0]["error"],
        )

    @patch("charm.TEMPEST_CONCURRENCY", "4")
    def test_validate_action_success(self):
        """Test validate action with default params."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        file1 = Mock()
        file1.name = "file_1"
        file2 = Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = Mock(
            return_value=[file1, file2]
        )
        exec_mock = Mock()
        self.harness.charm.pebble_handler().execute = exec_mock

        action_event = Mock()
        action_event.params = {
            "serial": False,
            "regex": "smoke",
            "exclude-regex": "",
            "test-list": "",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_not_called()
        exec_mock.assert_called_with(
            ["tempest-run-wrapper", "--parallel", "--regex", "smoke"],
            user="tempest",
            group="tempest",
            working_dir=TEMPEST_HOME,
            exception_on_error=True,
            environment=TEST_TEMPEST_ENV,
        )

    @patch("charm.TEMPEST_CONCURRENCY", "4")
    def test_validate_action_params(self):
        """Test validate action with more params."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        file1 = Mock()
        file1.name = "file_1"
        file2 = Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = Mock(
            return_value=[file1, file2]
        )
        exec_mock = Mock()
        self.harness.charm.pebble_handler().execute = exec_mock

        action_event = Mock()
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

    def test_validate_action_no_params(self):
        """Test validate action with no filter params."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        exec_mock = Mock()
        self.harness.charm.pebble_handler().execute = exec_mock

        action_event = Mock()
        action_event.params = {
            "serial": True,
            "regex": "",
            "exclude-regex": "",
            "test-list": "",
        }
        self.harness.charm._on_validate_action(action_event)
        action_event.fail.assert_called_once()
        self.assertIn(
            "No filter parameters provided",
            action_event.set_results.call_args.args[0]["error"],
        )
        exec_mock.assert_not_called()

    def test_get_list_action(self):
        """Test get-list action."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        file1 = Mock()
        file1.name = "file_1"
        file2 = Mock()
        file2.name = "file_2"
        self.harness.charm.pebble_handler().container.list_files = Mock(
            return_value=[file1, file2]
        )

        action_event = Mock()
        self.harness.charm._on_get_lists_action(action_event)
        action_event.fail.assert_not_called()

    def test_accounts_count_config_propagates_to_env(self):
        """tempest-accounts-count config appears in TEMPEST_ACCOUNTS_COUNT env."""
        self.add_identity_ops_relation(self.harness)
        env = self.harness.charm._get_environment_for_tempest(
            TempestEnvVariant.ADHOC
        )
        # default tempest-accounts-count is 8
        self.assertEqual(env["TEMPEST_ACCOUNTS_COUNT"], "8")

        self.harness.update_config({"tempest-accounts-count": "12"})
        env = self.harness.charm._get_environment_for_tempest(
            TempestEnvVariant.ADHOC
        )
        self.assertEqual(env["TEMPEST_ACCOUNTS_COUNT"], "12")

    def test_get_list_action_not_ready(self):
        """Test get-list action when pebble is not ready."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        file1 = Mock()
        file1.name = "file_1"
        file2 = Mock()
        file2.name = "file_2"
        self.harness.charm.unit.get_container(CONTAINER).can_connect = Mock(
            return_value=False
        )

        action_event = Mock()
        self.harness.charm._on_get_lists_action(action_event)
        action_event.fail.assert_called_with("pebble is not ready")

    def test_blocked_status_invalid_schedule(self):
        """Test to verify blocked status with invalid schedule config."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        self.harness.charm.is_tempest_ready = Mock(return_value=True)

        # invalid schedule should make charm in blocked status
        self.harness.update_config({"schedule": "* *"})
        self.assertIn("invalid schedule", self.harness.charm.status.message())
        self.assertEqual(self.harness.charm.status.status.name, "blocked")

        # updating the schedule to something valid should unblock it
        self.harness.update_config({"schedule": "*/20 * * * *"})
        self.assertEqual(self.harness.charm.status.message(), "")
        self.assertEqual(self.harness.charm.status.status.name, "active")

    def test_error_initing_tempest(self):
        """Test to verify blocked status if tempest init fails."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        self.harness.charm.peers = Mock()
        self.harness.charm.peers.interface.peers_rel.data = MagicMock()
        self.harness.charm.peers.interface.peers_rel.data.__getitem__.return_value = {
            TEMPEST_READY_KEY: ""
        }

        mock_pebble = Mock()
        mock_pebble.init_tempest = Mock(side_effect=RuntimeError)
        self.harness.charm.pebble_handler = Mock(return_value=mock_pebble)
        self.harness.charm.is_tempest_ready = Mock(return_value=False)

        self.harness.update_config({"schedule": "*/21 * * * *"})

        self.harness.charm.set_tempest_ready.assert_has_calls(
            [call(False), call(False)]
        )
        self.assertEqual(self.harness.charm.set_tempest_ready.call_count, 3)
        self.assertIn(
            "tempest init failed", self.harness.charm.status.message()
        )
        self.assertEqual(self.harness.charm.status.status.name, "blocked")

    def test_is_tempest_ready(self):
        """Test the tempest ready check method."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        # simulate tempest ready
        self.harness.charm.peers = Mock()
        self.harness.charm.peers.interface.peers_rel.data = MagicMock()
        self.harness.charm.peers.interface.peers_rel.data.__getitem__.return_value = {
            TEMPEST_READY_KEY: "true"
        }

        self.assertTrue(self.harness.charm.is_tempest_ready())

    def test_is_tempest_ready_false(self):
        """Test the tempest ready check method."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_logging_relation(self.harness)
        self.add_identity_ops_relation(self.harness)
        self.add_grafana_dashboard_relation(self.harness)

        # simulate tempest not ready
        self.harness.charm.peers = Mock()
        self.harness.charm.peers.interface.peers_rel.data = MagicMock()
        self.harness.charm.peers.interface.peers_rel.data.__getitem__.return_value = {
            TEMPEST_READY_KEY: ""
        }

        self.assertFalse(self.harness.charm.is_tempest_ready())

    def test_set_tempest_ready(self):
        """Test the tempest ready set method."""
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.charm.peers = Mock()
        self.harness.charm.set_tempest_ready(True)
        self.harness.charm.peers.set_unit_data.assert_called_with(
            {TEMPEST_READY_KEY: "true"}
        )

        self.harness.charm.peers = Mock()
        self.harness.charm.set_tempest_ready(False)
        self.harness.charm.peers.set_unit_data.assert_called_with(
            {TEMPEST_READY_KEY: ""}
        )

    def test_init_tempest_fail(self):
        """Test the tempest init method logic."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_identity_ops_relation(self.harness)

        # tempest init not run yet, pebble init tempest fails
        pebble_mock = Mock()
        pebble_mock.init_tempest = Mock(side_effect=RuntimeError)
        self.harness.charm.pebble_handler = Mock(return_value=pebble_mock)
        self.harness.charm.is_tempest_ready = Mock(return_value=False)
        self.harness.charm.set_tempest_ready = Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

    def test_init_tempest_success(self):
        """Test the tempest init method logic."""
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_identity_ops_relation(self.harness)

        # tempest init succeeds
        pebble_mock = Mock()
        pebble_mock.init_tempest = Mock()
        self.harness.charm.pebble_handler = Mock(return_value=pebble_mock)
        self.harness.charm.is_tempest_ready = Mock(return_value=False)
        self.harness.charm.set_tempest_ready = Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_called_once_with(True)

    def test_init_tempest_already_run(self):
        """Test the tempest init method logic."""
        test_utils.set_all_pebbles_ready(self.harness)

        # tempest init already run
        pebble_mock = Mock()
        pebble_mock.init_tempest = Mock()
        self.harness.charm.pebble_handler = Mock(return_value=pebble_mock)
        self.harness.charm.is_tempest_ready = Mock(return_value=True)
        self.harness.charm.set_tempest_ready = Mock()

        self.harness.charm.init_tempest()
        self.harness.charm.set_tempest_ready.assert_not_called()

    def test_start(self):
        """Test start charm updates things as required."""
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.charm.set_tempest_ready = Mock()
        self.harness.charm._on_start(Mock())
        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

    def test_upgrade_charm(self):
        """Test upgrade charm updates things as required."""
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.charm.set_tempest_ready = Mock()
        self.harness.charm._on_upgrade_charm(Mock())
        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

    def test_tempest_env_variant(self):
        """Test env variant for tempest returns correct path."""
        self.assertEqual(
            TempestEnvVariant.PERIODIC.output_path(), TEMPEST_PERIODIC_OUTPUT
        )
        self.assertEqual(
            TempestEnvVariant.ADHOC.output_path(), TEMPEST_ADHOC_OUTPUT
        )

    def test_remove_identity_triggers_tempest_no_longer_ready(self):
        """Removing the keystone relation causes tempest no longer ready."""
        test_utils.set_all_pebbles_ready(self.harness)
        identity_ops_rel_id = self.add_identity_ops_relation(self.harness)

        self.harness.charm.set_tempest_ready = Mock()

        self.harness.remove_relation(identity_ops_rel_id)

        self.harness.charm.set_tempest_ready.assert_called_once_with(False)

    @patch("utils.constants.cpu_count", Mock(return_value=2))
    def test_concurrency_calculation_less_cpus(self):
        """Test concurrency is calculated correctly with only 2 cpus."""
        self.assertEqual(get_tempest_concurrency(), "2")

    @patch("utils.constants.cpu_count", Mock(return_value=8))
    def test_concurrency_calculation_more_cpus(self):
        """Test concurrency is bounded to 4."""
        self.assertEqual(get_tempest_concurrency(), "4")

    def test_logging_ready(self):
        """Test logging relation ready."""
        rel_id = self.add_logging_relation(self.harness)

        # client endpoints found
        self.harness.charm.logging.interface._promtail_config.return_value = {
            "clients": [
                {
                    "url": "http://grafana-agent-k8s-endpoints:3500/loki/api/v1/push"
                }
            ],
            "other_key": "other_values",
        }
        self.assertEqual(self.harness.charm.logging.ready, True)

        # empty client endpoints
        self.harness.charm.logging.interface._promtail_config.return_value = {
            "clients": [],
            "other_key": "other_values",
        }
        self.assertEqual(self.harness.charm.logging.ready, False)

        # empty promtail config
        self.harness.remove_relation(rel_id)
        self.harness.charm.logging.interface._promtail_config.return_value = {}
        self.assertEqual(self.harness.charm.logging.ready, False)

    def _check_override_in_string(
        self,
        key: str,
        value: str,
        actual_string: str,
        should_be_present: bool = True,
    ):
        """Checks if a specific 'key value' pair is in the override string."""
        expected_pair = f"{key} {value}"
        if should_be_present:
            self.assertIn(
                expected_pair,
                actual_string,
                f"Expected '{expected_pair}' to be in '{actual_string}'",
            )
        else:
            self.assertNotIn(
                expected_pair,
                actual_string,
                f"Expected '{expected_pair}' NOT to be in '{actual_string}'",
            )

    def test_roles_default_overrides(self):
        """Verify role-based overrides when roles config is default (all roles)."""
        self.add_identity_ops_relation(self.harness)
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.update_config({"roles": "compute,control,storage"})

        env = self.harness.charm._get_environment_for_tempest(
            TempestEnvVariant.ADHOC
        )
        actual_combined_overrides = env.get("TEMPEST_CONFIG_OVERRIDES", "")

        self._check_override_in_string(
            "service_available.cinder",
            "false",
            actual_combined_overrides,
            should_be_present=False,
        )
        self._check_override_in_string(
            "service_available.nova",
            "false",
            actual_combined_overrides,
            should_be_present=False,
        )

    def test_roles_compute_only_overrides(self):
        """Verify role-based overrides when roles = 'compute'."""
        self.add_identity_ops_relation(self.harness)
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.update_config({"roles": "compute"})
        env = self.harness.charm._get_environment_for_tempest(
            TempestEnvVariant.ADHOC
        )
        actual_combined_overrides = env.get("TEMPEST_CONFIG_OVERRIDES", "")

        self._check_override_in_string(
            "service_available.cinder",
            "false",
            actual_combined_overrides,
            should_be_present=True,
        )
        self._check_override_in_string(
            "service_available.nova",
            "false",
            actual_combined_overrides,
            should_be_present=False,
        )

    def test_roles_storage_only_overrides(self):
        """Verify role-based overrides when roles = 'storage'."""
        self.add_identity_ops_relation(self.harness)
        test_utils.set_all_pebbles_ready(self.harness)

        self.harness.update_config({"roles": "storage"})
        env = self.harness.charm._get_environment_for_tempest(
            TempestEnvVariant.ADHOC
        )
        actual_combined_overrides = env.get("TEMPEST_CONFIG_OVERRIDES", "")

        self._check_override_in_string(
            "service_available.cinder",
            "false",
            actual_combined_overrides,
            should_be_present=False,
        )
        self._check_override_in_string(
            "service_available.nova",
            "false",
            actual_combined_overrides,
            should_be_present=True,
        )

    def test_roles_blank_raises(self):
        """Blank 'roles' config should raise a BlockedExceptionError."""
        with self.assertRaises(BlockedExceptionError):
            overrides._parse_roles_config("")

    def test_current_config_hash_changes(self):
        """Hash is stable for unchanged config and updates when roles/region change."""
        initial_roles = "compute,control"
        initial_region = "RegionOne"
        self.harness.update_config(
            {"roles": initial_roles, "region": initial_region}
        )
        hash_1 = self.harness.charm._current_config_hash()
        self.harness.update_config(
            {"roles": initial_roles, "region": initial_region}
        )
        hash_2 = self.harness.charm._current_config_hash()
        self.assertEqual(hash_1, hash_2)

        self.harness.update_config({"roles": "compute"})
        hash_3 = self.harness.charm._current_config_hash()
        self.assertNotEqual(hash_1, hash_3)

        self.harness.update_config(
            {"roles": initial_roles, "region": "RegionTwo"}
        )
        hash_4 = self.harness.charm._current_config_hash()
        self.assertNotEqual(hash_1, hash_4)
