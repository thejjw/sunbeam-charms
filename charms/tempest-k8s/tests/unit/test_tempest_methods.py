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

"""Unit tests for tempest-k8s charm internal methods.

These tests exercise charm methods directly (readiness, init, config
hashing, actions, env generation) by calling unbound methods with
mock ``self`` objects.
"""

from unittest.mock import (
    Mock,
    patch,
)

import charm
import ops.pebble
import pytest
from handlers import (
    TempestPebbleHandler,
)
from ops_sunbeam.guard import (
    BlockedExceptionError,
)
from utils import (
    overrides,
)
from utils.constants import (
    TEMPEST_ADHOC_OUTPUT,
    TEMPEST_HOME,
    TEMPEST_PERIODIC_OUTPUT,
    TEMPEST_READY_KEY,
    get_tempest_concurrency,
)
from utils.types import (
    TempestEnvVariant,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CREDENTIAL = {
    "username": "tempest",
    "password": "password",
    "domain-name": "tempest",
    "domain-id": "tempest-domain-id",
    "project-name": "CloudValidation-tempest",
    "auth-url": "http://10.6.0.23/openstack-keystone/v3",
}


def _mock_charm(**config_overrides):
    """Create a Mock that behaves like TempestOperatorCharm for method tests."""
    m = Mock()
    config = {
        "region": "RegionOne",
        "tempest-concurrency": 4,
        "tempest-accounts-count": 8,
        "roles": "compute,control,storage",
        "schedule": "0 */1 * * *",
    }
    config.update(config_overrides)
    m.config = config
    return m


# ---------------------------------------------------------------------------
# Pure function tests (no charm instance needed)
# ---------------------------------------------------------------------------


class TestTempestEnvVariant:
    """TempestEnvVariant.output_path returns the correct log path."""

    def test_periodic_output_path(self):
        """Test periodic output path."""
        assert (
            TempestEnvVariant.PERIODIC.output_path() == TEMPEST_PERIODIC_OUTPUT
        )

    def test_adhoc_output_path(self):
        """Test adhoc output path."""
        assert TempestEnvVariant.ADHOC.output_path() == TEMPEST_ADHOC_OUTPUT


class TestGetTempestConcurrency:
    """get_tempest_concurrency caps concurrency at cpu_count."""

    @patch("utils.constants.cpu_count", Mock(return_value=2))
    def test_less_cpus(self):
        """Test less cpus."""
        assert get_tempest_concurrency(4) == "2"

    @patch("utils.constants.cpu_count", Mock(return_value=8))
    def test_more_cpus(self):
        """Test more cpus."""
        assert get_tempest_concurrency(4) == "4"


class TestRolesConfig:
    """Blank roles config raises BlockedExceptionError."""

    def test_blank_raises(self):
        """Test blank raises."""
        with pytest.raises(BlockedExceptionError):
            overrides._parse_roles_config("")


# ---------------------------------------------------------------------------
# Charm method tests (unbound method with mock self)
# ---------------------------------------------------------------------------


class TestIsTempestReady:
    """is_tempest_ready reads the peer unit data flag."""

    def test_true_when_flag_set(self):
        """Test true when flag set."""
        m = _mock_charm()
        m.get_unit_data.return_value = "true"
        assert charm.TempestOperatorCharm.is_tempest_ready(m) is True

    def test_false_when_flag_empty(self):
        """Test false when flag empty."""
        m = _mock_charm()
        m.get_unit_data.return_value = ""
        assert charm.TempestOperatorCharm.is_tempest_ready(m) is False


class TestSetTempestReady:
    """set_tempest_ready writes to peer unit data."""

    def test_set_true(self):
        """Test set true."""
        m = _mock_charm()
        charm.TempestOperatorCharm.set_tempest_ready(m, True)
        m.peers.set_unit_data.assert_called_once_with(
            {TEMPEST_READY_KEY: "true"}
        )

    def test_set_false(self):
        """Test set false."""
        m = _mock_charm()
        charm.TempestOperatorCharm.set_tempest_ready(m, False)
        m.peers.set_unit_data.assert_called_once_with({TEMPEST_READY_KEY: ""})


class TestInitTempest:
    """init_tempest orchestration logic."""

    def test_already_run_skips(self):
        """Test already run skips."""
        m = _mock_charm()
        m.is_tempest_ready.return_value = True
        charm.TempestOperatorCharm.init_tempest(m)
        m.set_tempest_ready.assert_not_called()

    def test_success_sets_ready(self):
        """Test success sets ready."""
        m = _mock_charm()
        m.is_tempest_ready.return_value = False
        charm.TempestOperatorCharm.init_tempest(m)
        m.set_tempest_ready.assert_called_once_with(True)

    def test_init_failure_clears_ready(self):
        """Test init failure clears ready."""
        m = _mock_charm()
        m.is_tempest_ready.return_value = False
        m.pebble_handler.return_value.init_tempest.side_effect = RuntimeError
        charm.TempestOperatorCharm.init_tempest(m)
        m.set_tempest_ready.assert_called_once_with(False)

    def test_cleanup_failure_clears_ready(self):
        """Test cleanup failure clears ready."""
        m = _mock_charm()
        m.is_tempest_ready.return_value = False
        m.pebble_handler.return_value.run_extensive_cleanup.side_effect = (
            ops.pebble.ExecError(["cmd"], 1, None, None)
        )
        charm.TempestOperatorCharm.init_tempest(m)
        m.set_tempest_ready.assert_called_once_with(False)


class TestCurrentConfigHash:
    """_current_config_hash is stable and changes with config."""

    def test_stable_for_same_config(self):
        """Test stable for same config."""
        m = _mock_charm()
        m._get_relevant_tempest_config_values.return_value = {
            "roles": "compute,control",
            "region": "RegionOne",
            "tempest-concurrency": 4,
            "tempest-accounts-count": "8",
        }
        h1 = charm.TempestOperatorCharm._current_config_hash(m)
        h2 = charm.TempestOperatorCharm._current_config_hash(m)
        assert h1 == h2

    def test_changes_with_roles(self):
        """Test changes with roles."""
        m = _mock_charm()
        m._get_relevant_tempest_config_values.return_value = {
            "roles": "compute,control",
            "region": "RegionOne",
            "tempest-concurrency": 4,
            "tempest-accounts-count": "8",
        }
        h1 = charm.TempestOperatorCharm._current_config_hash(m)

        m._get_relevant_tempest_config_values.return_value = {
            "roles": "compute",
            "region": "RegionOne",
            "tempest-concurrency": 4,
            "tempest-accounts-count": "8",
        }
        h2 = charm.TempestOperatorCharm._current_config_hash(m)
        assert h1 != h2

    def test_changes_with_region(self):
        """Test changes with region."""
        m = _mock_charm()
        m._get_relevant_tempest_config_values.return_value = {
            "roles": "compute,control",
            "region": "RegionOne",
            "tempest-concurrency": 4,
            "tempest-accounts-count": "8",
        }
        h1 = charm.TempestOperatorCharm._current_config_hash(m)

        m._get_relevant_tempest_config_values.return_value = {
            "roles": "compute,control",
            "region": "RegionTwo",
            "tempest-concurrency": 4,
            "tempest-accounts-count": "8",
        }
        h2 = charm.TempestOperatorCharm._current_config_hash(m)
        assert h1 != h2


# ---------------------------------------------------------------------------
# Environment generation tests
# ---------------------------------------------------------------------------


class TestGetEnvironmentForTempest:
    """_get_environment_for_tempest builds the right env dict."""

    def _make_charm_with_credential(self, **config_overrides):
        m = _mock_charm(**config_overrides)
        m.user_id_ops.get_user_credential.return_value = CREDENTIAL
        m._get_proxy_environment.return_value = {}
        m._get_os_cacert_environment.return_value = {}
        m._get_overrides_for_tempest_conf.return_value = ""
        return m

    def test_accounts_count_default(self):
        """Test accounts count default."""
        m = self._make_charm_with_credential()
        env = charm.TempestOperatorCharm._get_environment_for_tempest(
            m, TempestEnvVariant.ADHOC
        )
        assert env["TEMPEST_ACCOUNTS_COUNT"] == "8"

    def test_accounts_count_custom(self):
        """Test accounts count custom."""
        m = self._make_charm_with_credential()
        m.config["tempest-accounts-count"] = 12
        env = charm.TempestOperatorCharm._get_environment_for_tempest(
            m, TempestEnvVariant.ADHOC
        )
        assert env["TEMPEST_ACCOUNTS_COUNT"] == "12"

    @patch("utils.constants.cpu_count", Mock(return_value=8))
    def test_concurrency_default(self):
        """Test concurrency default."""
        m = self._make_charm_with_credential()
        env = charm.TempestOperatorCharm._get_environment_for_tempest(
            m, TempestEnvVariant.ADHOC
        )
        assert env["TEMPEST_CONCURRENCY"] == "4"

    @patch("utils.constants.cpu_count", Mock(return_value=8))
    def test_concurrency_custom(self):
        """Test concurrency custom."""
        m = self._make_charm_with_credential()
        m.config["tempest-concurrency"] = 1
        env = charm.TempestOperatorCharm._get_environment_for_tempest(
            m, TempestEnvVariant.ADHOC
        )
        assert env["TEMPEST_CONCURRENCY"] == "1"


# ---------------------------------------------------------------------------
# Roles-based overrides tests
# ---------------------------------------------------------------------------


class TestRolesOverrides:
    """_get_overrides_for_tempest_conf includes/excludes role overrides."""

    def test_default_all_roles(self):
        """Test default all roles."""
        m = _mock_charm(roles="compute,control,storage")
        result = charm.TempestOperatorCharm._get_overrides_for_tempest_conf(m)
        assert "service_available.cinder false" not in result
        assert "service_available.nova false" not in result

    def test_compute_only(self):
        """Test compute only."""
        m = _mock_charm(roles="compute")
        result = charm.TempestOperatorCharm._get_overrides_for_tempest_conf(m)
        assert "service_available.cinder false" in result
        assert "service_available.nova false" not in result

    def test_storage_only(self):
        """Test storage only."""
        m = _mock_charm(roles="storage")
        result = charm.TempestOperatorCharm._get_overrides_for_tempest_conf(m)
        assert "service_available.cinder false" not in result
        assert "service_available.nova false" in result


# ---------------------------------------------------------------------------
# Config context tests
# ---------------------------------------------------------------------------


class TestTempestConfigurationContext:
    """TempestConfigurationContext.context reflects schedule readiness."""

    def _make_context(self, schedule, is_ready):
        from utils.validators import (
            validated_schedule,
        )

        m = _mock_charm(schedule=schedule)
        m.get_schedule.return_value = validated_schedule(schedule)
        m.is_schedule_ready.return_value = is_ready
        return charm.TempestConfigurationContext(m, "tempest")

    def test_valid_schedule_when_ready(self):
        """Test valid schedule when ready."""
        ctx = self._make_context("0 0 */7 * *", is_ready=True)
        assert ctx.context()["schedule"] == "0 0 */7 * *"

    def test_empty_when_not_ready(self):
        """Test empty when not ready."""
        ctx = self._make_context("0 0 */7 * *", is_ready=False)
        assert ctx.context()["schedule"] == ""

    def test_empty_when_schedule_disabled(self):
        """Test empty when schedule disabled."""
        ctx = self._make_context("", is_ready=False)
        assert ctx.context()["schedule"] == ""

    def test_empty_when_schedule_invalid(self):
        """Test empty when schedule invalid."""
        ctx = self._make_context("* * * * *", is_ready=False)
        assert ctx.context()["schedule"] == ""


# ---------------------------------------------------------------------------
# Pebble handler method tests (run_tempest_tests validation)
# ---------------------------------------------------------------------------


class TestRunTempestTests:
    """run_tempest_tests validates inputs before execution."""

    def test_no_params_raises(self):
        """Test no params raises."""
        handler = Mock()
        with pytest.raises(RuntimeError, match="No filter parameters"):
            TempestPebbleHandler.run_tempest_tests(
                handler, [], "", "", False, {}
            )

    def test_invalid_regex_raises(self):
        """Test invalid regex raises."""
        handler = Mock()
        with pytest.raises(RuntimeError, match="invalid regex"):
            TempestPebbleHandler.run_tempest_tests(
                handler, ["test("], "", "", False, {}
            )

    def test_unknown_list_raises(self):
        """Test unknown list raises."""
        handler = Mock()
        handler.get_test_lists.return_value = ["file_1", "file_2"]
        with pytest.raises(RuntimeError, match="not a known test list"):
            TempestPebbleHandler.run_tempest_tests(
                handler, [], "", "nonexistent", False, {}
            )

    def test_success_calls_execute(self):
        """Test success calls execute."""
        handler = Mock()
        TempestPebbleHandler.run_tempest_tests(
            handler, ["smoke"], "", "", False, {}
        )
        handler.execute.assert_called_once_with(
            ["tempest-run-wrapper", "--parallel", "--regex", "smoke"],
            user="tempest",
            group="tempest",
            working_dir=TEMPEST_HOME,
            exception_on_error=True,
            environment={},
        )

    def test_params_build_command(self):
        """Test params build command."""
        handler = Mock()
        handler.get_test_lists.return_value = ["file_1", "file_2"]
        TempestPebbleHandler.run_tempest_tests(
            handler, ["re1", "re2"], "excludethis", "file_1", True, {}
        )
        handler.execute.assert_called_once_with(
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
            environment={},
        )

    def test_pebble_not_ready_raises(self):
        """Test pebble not ready raises."""
        handler = Mock()
        handler.pebble_ready = False
        with pytest.raises(RuntimeError, match="pebble is not ready"):
            TempestPebbleHandler.run_tempest_tests(
                handler, ["smoke"], "", "", False, {}
            )


# ---------------------------------------------------------------------------
# Action handler tests
# ---------------------------------------------------------------------------


class TestValidateAction:
    """_on_validate_action sets results and fails on error."""

    def test_invalid_regex_fails_action(self):
        """Test invalid regex fails action."""
        m = _mock_charm()
        m.user_id_ops.get_user_credential.return_value = CREDENTIAL
        m._get_proxy_environment.return_value = {}
        m._get_os_cacert_environment.return_value = {}
        m._get_overrides_for_tempest_conf.return_value = ""
        m.pebble_handler.return_value.run_tempest_tests.side_effect = (
            RuntimeError("'test(' is an invalid regex: ...")
        )

        event = Mock()
        event.params = {
            "serial": False,
            "regex": "test(",
            "exclude-regex": "",
            "test-list": "",
        }
        charm.TempestOperatorCharm._on_validate_action(m, event)
        event.fail.assert_called_once()
        assert "invalid regex" in event.set_results.call_args.args[0]["error"]

    def test_no_params_fails_action(self):
        """Test no params fails action."""
        m = _mock_charm()
        m.user_id_ops.get_user_credential.return_value = CREDENTIAL
        m._get_proxy_environment.return_value = {}
        m._get_os_cacert_environment.return_value = {}
        m._get_overrides_for_tempest_conf.return_value = ""
        m.pebble_handler.return_value.run_tempest_tests.side_effect = (
            RuntimeError("No filter parameters provided")
        )

        event = Mock()
        event.params = {
            "serial": True,
            "regex": "",
            "exclude-regex": "",
            "test-list": "",
        }
        charm.TempestOperatorCharm._on_validate_action(m, event)
        event.fail.assert_called_once()
        assert (
            "No filter parameters"
            in event.set_results.call_args.args[0]["error"]
        )

    def test_success_sets_results(self):
        """Test success sets results."""
        m = _mock_charm()
        m.user_id_ops.get_user_credential.return_value = CREDENTIAL
        m._get_proxy_environment.return_value = {}
        m._get_os_cacert_environment.return_value = {}
        m._get_overrides_for_tempest_conf.return_value = ""
        m.pebble_handler.return_value.run_tempest_tests.return_value = (
            "Tests passed"
        )
        m.get_copy_log_cmd.return_value = "juju scp ..."

        event = Mock()
        event.params = {
            "serial": False,
            "regex": "smoke",
            "exclude-regex": "",
            "test-list": "",
        }
        charm.TempestOperatorCharm._on_validate_action(m, event)
        event.fail.assert_not_called()
        assert "summary" in event.set_results.call_args.args[0]


class TestGetListsAction:
    """_on_get_lists_action returns lists or fails."""

    def test_success(self):
        """Test success."""
        m = _mock_charm()
        m.pebble_handler.return_value.get_test_lists.return_value = [
            "file_1",
            "file_2",
        ]
        event = Mock()
        charm.TempestOperatorCharm._on_get_lists_action(m, event)
        event.fail.assert_not_called()

    def test_not_ready_fails(self):
        """Test not ready fails."""
        m = _mock_charm()
        m.pebble_handler.return_value.get_test_lists.side_effect = (
            RuntimeError("pebble is not ready")
        )
        event = Mock()
        charm.TempestOperatorCharm._on_get_lists_action(m, event)
        event.fail.assert_called_once_with("pebble is not ready")


# ---------------------------------------------------------------------------
# Logging handler readiness
# ---------------------------------------------------------------------------


class TestLoggingReady:
    """LoggingRelationHandler.ready checks promtail config."""

    def _make_handler(self):
        from handlers import (
            LoggingRelationHandler,
        )

        handler = LoggingRelationHandler.__new__(LoggingRelationHandler)
        handler.interface = Mock()
        return handler

    def test_ready_with_clients(self):
        """Test ready with clients."""
        handler = self._make_handler()
        handler.interface._promtail_config.return_value = {
            "clients": [{"url": "http://loki:3500/loki/api/v1/push"}],
        }
        assert handler.ready is True

    def test_not_ready_empty_clients(self):
        """Test not ready empty clients."""
        handler = self._make_handler()
        handler.interface._promtail_config.return_value = {
            "clients": [],
        }
        assert handler.ready is False

    def test_not_ready_no_config(self):
        """Test not ready no config."""
        handler = self._make_handler()
        handler.interface._promtail_config.return_value = {}
        assert handler.ready is False
