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
from typing import (
    Dict,
    List,
)

import ops
import ops.charm
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
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
    BlockedStatus,
    MaintenanceStatus,
)
from utils.constants import (
    CONTAINER,
    TEMPEST_CONCURRENCY,
    TEMPEST_CONF,
    TEMPEST_HOME,
    TEMPEST_LIST_DIR,
    TEMPEST_OUTPUT,
    TEMPEST_READY_KEY,
    TEMPEST_TEST_ACCOUNTS,
    TEMPEST_WORKSPACE,
    TEMPEST_WORKSPACE_PATH,
)

logger = logging.getLogger(__name__)


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
        )
        handlers.append(self.user_id_ops)
        self.loki = LoggingRelationHandler(
            self,
            "logging",
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

    def _get_environment_for_tempest(self) -> Dict[str, str]:
        """Return a dictionary of environment variables.

        To be used with pebble commands that run tempest discover, etc.
        """
        logger.debug("Retrieving OpenStack credentials")
        credential = self.user_id_ops.get_user_credential()
        return {
            "OS_REGION_NAME": "RegionOne",
            "OS_IDENTITY_API_VERSION": "3",
            "OS_AUTH_VERSION": "3",
            "OS_AUTH_URL": credential.get("auth-url"),
            "OS_USERNAME": credential.get("username"),
            "OS_PASSWORD": credential.get("password"),
            "OS_USER_DOMAIN_NAME": credential.get("domain-name"),
            "OS_PROJECT_NAME": credential.get("project-name"),
            "OS_PROJECT_DOMAIN_NAME": credential.get("domain-name"),
            "OS_DOMAIN_NAME": credential.get("domain-name"),
            "TEMPEST_CONCURRENCY": TEMPEST_CONCURRENCY,
            "TEMPEST_CONF": TEMPEST_CONF,
            "TEMPEST_HOME": TEMPEST_HOME,
            "TEMPEST_LIST_DIR": TEMPEST_LIST_DIR,
            "TEMPEST_OUTPUT": TEMPEST_OUTPUT,
            "TEMPEST_TEST_ACCOUNTS": TEMPEST_TEST_ACCOUNTS,
            "TEMPEST_WORKSPACE": TEMPEST_WORKSPACE,
            "TEMPEST_WORKSPACE_PATH": TEMPEST_WORKSPACE_PATH,
        }

    def is_tempest_ready(self) -> bool:
        """Check if the tempest environment has been set up by the charm."""
        return bool(self.leader_get(TEMPEST_READY_KEY))

    def set_tempest_ready(self, ready: bool):
        """Set tempest readiness state."""
        self.leader_set({TEMPEST_READY_KEY: "true" if ready else ""})

    def init_tempest(self) -> bool:
        """Init tempest environment for the charm.

        This will skip running the steps if it was previously run to success.

        Returns a boolean indicating success or failure::
        True if tempest environment is in a ready state, False if not.
        """
        if self.is_tempest_ready():
            logger.debug(
                "Skipping tempest init because it is already completed"
            )
            return True

        env = self._get_environment_for_tempest()
        pebble = self.pebble_handler()
        try:
            pebble.init_tempest(env)
        except RuntimeError:
            self.set_tempest_ready(False)
            return False

        self.set_tempest_ready(True)
        return True

    def post_config_setup(self) -> None:
        """Configuration steps after services have been setup."""
        logger.debug("Running post config setup")

        self.status.set(MaintenanceStatus("tempest init in progress"))

        success = self.init_tempest()

        if success:
            self.status.set(ActiveStatus(""))
        else:
            self.status.set(
                BlockedStatus("tempest init failed, see logs for more info")
            )
        logger.debug("Finish post config setup")

    def pebble_handler(self) -> TempestPebbleHandler:
        """Get the pebble handler."""
        return self.get_named_pebble_handler(CONTAINER)

    def _on_validate_action(self, event: ops.charm.ActionEvent) -> None:
        """Run tempest action."""
        serial: bool = event.params["serial"]
        regexes: List[str] = event.params["regex"].strip().split()
        exclude_regex: str = event.params["exclude-regex"].strip()
        test_list: str = event.params["test-list"].strip()

        env = self._get_environment_for_tempest()
        try:
            output = self.pebble_handler().run_tempest_tests(
                regexes, exclude_regex, test_list, serial, env
            )
        except RuntimeError as e:
            event.fail(str(e))
            # still print the message,
            # because it could be a lot of output from tempest,
            # and we want it neatly formatted
            print(e)
            return
        print(output)

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
