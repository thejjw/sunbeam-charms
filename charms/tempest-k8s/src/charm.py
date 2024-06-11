#!/usr/bin/env python3
#
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

"""Tempest Operator Charm.

This charm provide Tempest as part of an OpenStack deployment
"""

import logging
import os
from functools import (
    cache,
)
from typing import (
    Dict,
    List,
    Optional,
)

import ops
import ops.charm
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from handlers import (
    GrafanaDashboardRelationHandler,
    LoggingRelationHandler,
    TempestPebbleHandler,
    TempestUserIdentityRelationHandler,
)
from ops.main import (
    main,
)
from ops.model import (
    ActiveStatus,
    MaintenanceStatus,
)
from ops_sunbeam.config_contexts import (
    ConfigContext,
)
from utils.alert_rules import (
    ensure_alert_rules_disabled,
    update_alert_rules_files,
)
from utils.cleanup import (
    CleanUpError,
    run_extensive_cleanup,
)
from utils.constants import (
    CONTAINER,
    TEMPEST_ACCOUNTS_COUNT,
    TEMPEST_ADHOC_OUTPUT,
    TEMPEST_CONCURRENCY,
    TEMPEST_CONF,
    TEMPEST_EXCLUDE_LIST,
    TEMPEST_HOME,
    TEMPEST_LIST_DIR,
    TEMPEST_READY_KEY,
    TEMPEST_TEST_ACCOUNTS,
    TEMPEST_WORKSPACE,
    TEMPEST_WORKSPACE_PATH,
)
from utils.types import (
    TempestEnvVariant,
)
from utils.validators import (
    Schedule,
    validated_schedule,
)

LOKI_RELATION_NAME = "logging"

logger = logging.getLogger(__name__)


class TempestConfigurationContext(ConfigContext):
    """Configuration context for tempest."""

    def context(self) -> dict:
        """Tempest context."""
        return {
            "schedule": (
                self.charm.get_schedule().value
                if self.charm.is_schedule_ready()
                else ""
            ),
        }


class TempestOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "tempest"

    mandatory_relations = {"identity-ops"}

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run the constructor."""
        # config for openstack, used by tempest
        super().__init__(framework)
        self.framework.observe(
            self.on.validate_action, self._on_validate_action
        )
        self.framework.observe(
            self.on.get_lists_action, self._on_get_lists_action
        )
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the operator."""
        return [
            # crontab is owned and run by root
            sunbeam_core.ContainerConfigFile("/etc/crontab", "root", "root"),
            # Only give exec access to root and tempest user
            # for these wrappers, simply for principle of least privilege.
            sunbeam_core.ContainerConfigFile(
                "/usr/local/sbin/tempest-run-wrapper",
                "root",
                "tempest",
                0o750,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/sbin/tempest-init",
                "root",
                "tempest",
                0o750,
            ),
        ]

    @cache
    def get_schedule(self) -> Schedule:
        """Validate and return the schedule from config.

        This is cached so the validation only needs to happen once.
        It can be cached because the config option will not change
        during a charm invocation.
        """
        return validated_schedule(self.config["schedule"])

    def is_schedule_ready(self) -> bool:
        """Check if the schedule is valid and periodic tests should be enabled.

        Return True if the schedule config option is valid,
        and pre-requisites for periodic checks are ready.
        """
        schedule = self.get_schedule()
        return (
            schedule.valid
            and schedule.value
            and self.is_tempest_ready()
            and self.loki.ready
            and self.user_id_ops.ready
        )

    @property
    def config_contexts(self) -> List[ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        return [TempestConfigurationContext(self, "tempest")]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for operator."""
        return [
            TempestPebbleHandler(
                self,
                CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.user_id_ops = TempestUserIdentityRelationHandler(
            self,
            "identity-ops",
            self.configure_charm,
            mandatory="identity-ops" in self.mandatory_relations,
            region=self.config["region"],
        )
        handlers.append(self.user_id_ops)
        self.loki = LoggingRelationHandler(
            self,
            LOKI_RELATION_NAME,
            self.configure_charm,
            mandatory="logging" in self.mandatory_relations,
        )
        handlers.append(self.loki)
        self.grafana = GrafanaDashboardRelationHandler(
            self,
            "grafana-dashboard",
            self.configure_charm,
            mandatory="grafana-dashboard" in self.mandatory_relations,
        )
        handlers.append(self.grafana)
        return handlers

    def _get_proxy_environment(self) -> Dict[str, str]:
        """Return dictionary with proxy settings."""
        juju_proxy_vars = [
            "JUJU_CHARM_HTTP_PROXY",
            "JUJU_CHARM_HTTPS_PROXY",
            "JUJU_CHARM_NO_PROXY",
        ]
        return {
            proxy_var.removeprefix("JUJU_CHARM_"): value
            for proxy_var in juju_proxy_vars
            if (value := os.environ.get(proxy_var))
        }

    def _get_environment_for_tempest(
        self, variant: TempestEnvVariant
    ) -> Dict[str, str]:
        """Return a dictionary of environment variables.

        To be used with pebble commands that run tempest discover, etc.
        """
        logger.debug("Retrieving OpenStack credentials")
        credential = self.user_id_ops.get_user_credential()
        tempest_env = {
            "OS_REGION_NAME": self.config["region"],
            "OS_IDENTITY_API_VERSION": "3",
            "OS_AUTH_VERSION": "3",
            "OS_AUTH_URL": credential.get("auth-url"),
            "OS_USERNAME": credential.get("username"),
            "OS_PASSWORD": credential.get("password"),
            "OS_USER_DOMAIN_NAME": credential.get("domain-name"),
            "OS_PROJECT_NAME": credential.get("project-name"),
            "OS_PROJECT_DOMAIN_NAME": credential.get("domain-name"),
            "OS_DOMAIN_NAME": credential.get("domain-name"),
            "OS_DOMAIN_ID": credential.get("domain-id"),
            "OS_USER_DOMAIN_ID": credential.get("domain-id"),
            "OS_PROJECT_DOMAIN_ID": credential.get("domain-id"),
            "TEMPEST_CONCURRENCY": TEMPEST_CONCURRENCY,
            "TEMPEST_ACCOUNTS_COUNT": TEMPEST_ACCOUNTS_COUNT,
            "TEMPEST_CONF": TEMPEST_CONF,
            "TEMPEST_EXCLUDE_LIST": TEMPEST_EXCLUDE_LIST,
            "TEMPEST_HOME": TEMPEST_HOME,
            "TEMPEST_LIST_DIR": TEMPEST_LIST_DIR,
            "TEMPEST_TEST_ACCOUNTS": TEMPEST_TEST_ACCOUNTS,
            "TEMPEST_WORKSPACE": TEMPEST_WORKSPACE,
            "TEMPEST_WORKSPACE_PATH": TEMPEST_WORKSPACE_PATH,
            "TEMPEST_OUTPUT": variant.output_path(),
        }
        tempest_env.update(self._get_proxy_environment())
        return tempest_env

    def _get_cleanup_env(self) -> Dict[str, str]:
        """Return a dictionary of environment variables.

        To be used with tempest resource cleanup functions.
        """
        logger.debug("Retrieving OpenStack credentials")
        credential = self.user_id_ops.get_user_credential()
        cleanup_env = {
            "OS_AUTH_URL": credential.get("auth-url"),
            "OS_USERNAME": credential.get("username"),
            "OS_PASSWORD": credential.get("password"),
            "OS_PROJECT_NAME": credential.get("project-name"),
            "OS_DOMAIN_ID": credential.get("domain-id"),
            "OS_USER_DOMAIN_ID": credential.get("domain-id"),
            "OS_PROJECT_DOMAIN_ID": credential.get("domain-id"),
        }
        cleanup_env.update(self._get_proxy_environment())
        return cleanup_env

    def get_unit_data(self, key: str) -> Optional[str]:
        """Retrieve a value set for this unit on the peer relation."""
        return self.peers.interface.peers_rel.data[self.unit].get(key)

    def is_tempest_ready(self) -> bool:
        """Check if the tempest environment has been set up by the charm."""
        return bool(self.get_unit_data(TEMPEST_READY_KEY))

    def set_tempest_ready(self, ready: bool):
        """Set tempest readiness state."""
        self.peers.set_unit_data({TEMPEST_READY_KEY: "true" if ready else ""})

    def init_tempest(self):
        """Init tempest environment for the charm.

        This will skip running the steps if it was previously run to success.
        It will also set the tempest readiness state key based on the outcome.
        """
        if self.is_tempest_ready():
            logger.debug(
                "Skipping tempest init because it is already completed"
            )
            return

        # This is environment sent to the scheduler service,
        # for periodic checks.
        env = self._get_environment_for_tempest(TempestEnvVariant.PERIODIC)
        pebble = self.pebble_handler()

        try:
            # do an extensive clean-up before tempest init to remove stalled resources
            run_extensive_cleanup(self._get_cleanup_env())
        except CleanUpError:
            logger.debug("Clean-up failed and tempest init not run.")
            self.set_tempest_ready(False)
            return

        try:
            pebble.init_tempest(env)
        except RuntimeError:
            self.set_tempest_ready(False)
            return

        self.set_tempest_ready(True)

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Custom configuration steps for this unit."""
        super().configure_unit(event)

        logger.info("Configuring the tempest environment")

        schedule = self.get_schedule()
        if not schedule.valid:
            raise sunbeam_guard.BlockedExceptionError(
                f"invalid schedule config: {schedule.err}"
            )

        self.status.set(MaintenanceStatus("tempest init in progress"))
        self.init_tempest()

        if not self.is_tempest_ready():
            logger.warning(
                "Tempest environment init failed, deferring event to retry."
            )
            event.defer()
            raise sunbeam_guard.BlockedExceptionError(
                "tempest init failed, see logs for more info"
            )

        # Ensure the alert rules are in sync with charm config.
        if self.is_schedule_ready():
            update_alert_rules_files(schedule)
        else:
            ensure_alert_rules_disabled()

        if self.loki.ready:
            for relation in self.model.relations[LOKI_RELATION_NAME]:
                self.loki.interface._handle_alert_rules(relation)

        self.status.set(ActiveStatus(""))
        logger.info("Finished configuring the tempest environment")

    def pebble_handler(self) -> TempestPebbleHandler:
        """Get the pebble handler."""
        return self.get_named_pebble_handler(CONTAINER)

    def _on_upgrade_charm(self, event: ops.charm.UpgradeCharmEvent) -> None:
        """Called on charm upgrade."""
        # When a charm is upgraded, consider tempest to no longer be ready,
        # so that in the follow up config-changed hook,
        # the charm will re-init tempest.
        self.set_tempest_ready(False)

    def _on_validate_action(self, event: ops.charm.ActionEvent) -> None:
        """Run tempest action."""
        serial: bool = event.params["serial"]
        regexes: List[str] = event.params["regex"].split()
        exclude_regex: str = event.params["exclude-regex"].strip()
        test_list: str = event.params["test-list"].strip()

        env = self._get_environment_for_tempest(TempestEnvVariant.ADHOC)

        try:
            summary = self.pebble_handler().run_tempest_tests(
                regexes, exclude_regex, test_list, serial, env
            )
        except RuntimeError as e:
            event.set_results({"error": str(e)})
            event.fail()
            return

        event.set_results(
            {
                "summary": summary,
                "info": (
                    "For detailed results, copy the log file from the container by running:\n"
                    + self.get_copy_log_cmd()
                ),
            }
        )

    def get_copy_log_cmd(self) -> str:
        """Get the juju command to copy the ad-hoc tempest log locally."""
        return f"$ juju scp -m {self.model.name} --container {CONTAINER} {self.unit.name}:{TEMPEST_ADHOC_OUTPUT} validation.log"

    def _on_get_lists_action(self, event: ops.charm.ActionEvent) -> None:
        """List tempest test lists action."""
        try:
            lists = self.pebble_handler().get_test_lists()
        except RuntimeError as e:
            event.fail(str(e))
            return
        # display neatly to the user.  This will also end up in the action output results.stdout
        print("\n".join(lists))


if __name__ == "__main__":
    main(TempestOperatorCharm)
