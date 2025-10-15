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
"""Handers for the tempest charm."""
import hashlib
import json
import logging
import os
import re
import secrets
import string
from functools import (
    wraps,
)
from typing import (
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
)

import charms.grafana_k8s.v0.grafana_dashboard as grafana_dashboard
import charms.loki_k8s.v1.loki_push_api as loki_push_api
import ops
import ops.model
import ops.pebble
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from utils.alert_rules import (
    ALERT_RULES_PATH,
)
from utils.constants import (
    OPENSTACK_DOMAIN,
    OPENSTACK_PROJECT,
    OPENSTACK_ROLE,
    OPENSTACK_USER,
    TEMPEST_ADHOC_OUTPUT,
    TEMPEST_HOME,
    TEMPEST_LIST_DIR,
    TEMPEST_PERIODIC_OUTPUT,
)

logger = logging.getLogger(__name__)


def assert_ready(f):
    """Decorator for gating pebble handler methods for readiness.

    Raise a runtime error if the pebble handler is not ready.
    """

    @wraps(f)
    def wrapper(self, *args, **kwargs):
        if not self.pebble_ready:
            raise RuntimeError("pebble is not ready")
        return f(self, *args, **kwargs)

    return wrapper


@sunbeam_tracing.trace_type
class TempestPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for the container."""

    PERIODIC_TEST_RUNNER = "periodic-test"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.container = self.charm.unit.get_container(self.container_name)

    def get_layer(self) -> dict:
        """Pebble configuration layer for the container."""
        return {
            "summary": "Periodic cloud validation service",
            "description": "Pebble config layer for periodic cloud validation job",
            "services": {
                # Note: cron service is started when the charm is ready,
                # but the cronjobs will only be configured to run
                # when the right conditions are met
                # (eg. observability connected, configuration set to run).
                self.service_name: {
                    "override": "replace",
                    "summary": "crontab to wake up pebble periodically for running periodic checks",
                    # Must run cron in foreground to be managed by pebble
                    "command": "cron -f",
                    "user": "root",
                    "group": "root",
                    "startup": "enabled",
                },
                self.PERIODIC_TEST_RUNNER: {
                    "override": "replace",
                    "summary": "Running tempest periodically",
                    "working-dir": TEMPEST_HOME,
                    "command": f"/usr/local/sbin/tempest-run-wrapper --load-list {TEMPEST_LIST_DIR}/readonly-quick",
                    "user": "tempest",
                    "group": "tempest",
                    "startup": "disabled",
                    "on-success": "ignore",
                    "on-failure": "ignore",
                },
            },
        }

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running.

        Override because we only want the cron service to be auto managed.
        """
        if not self.pebble_ready:
            return False
        services = self.container.get_services(self.service_name)
        return all([s.is_running() for s in services.values()])

    def start_all(self, restart: bool = True) -> None:
        """Start services in container.

        Override because we only want the cron service to be auto managed.

        :param restart: Whether to stop services before starting them.
        """
        if not self.container.can_connect():
            logger.debug(
                f"Container {self.container_name} not ready, deferring restart"
            )
            return
        services = self.container.get_services(self.service_name)
        for service_name, service in services.items():
            if not service.is_running():
                logger.debug(
                    f"Starting {service_name} in {self.container_name}"
                )
                self.container.start(service_name)
                continue

            if restart:
                logger.debug(
                    f"Restarting {service_name} in {self.container_name}"
                )
                self.container.restart(service_name)

    @assert_ready
    def get_test_lists(self) -> List[str]:
        """Get the filenames of available test lists."""
        files = self.container.list_files(TEMPEST_LIST_DIR)
        return [x.name for x in files]

    @assert_ready
    def push_auxiliary_files(self) -> None:
        """Push auxiliary files to the container.

        The auxiliary files are:
        * the cleanup script
        * the create_system_accounts.py script
        * the exclude list for tempest

        The create_system_accounts.py script is for creating the system admin
        accounts for the Ironic tempest tests.
        """
        aux_files = [
            "src/utils/cleanup.py",
            "src/utils/create_system_accounts.py",
            "src/utils/tempest_exclude_list.txt",
        ]
        for filename in aux_files:
            with open(filename) as f:
                self.container.push(
                    f"{TEMPEST_HOME}/{os.path.basename(filename)}",
                    f,
                    user="tempest",
                    group="tempest",
                    make_dirs=True,
                )

    @assert_ready
    def init_tempest(self, env: Dict[str, str]):
        """Init the openstack environment for tempest.

        Raise a RuntimeError if something goes wrong.
        """
        # Pebble runs cron, which runs tempest periodically
        # when periodic checks are enabled.
        # This ensures that tempest gets the env, inherited from cron.
        logger.debug("Adding environment to periodic service")
        layer = self.get_layer()
        layer["services"][self.PERIODIC_TEST_RUNNER]["environment"] = env
        self.container.add_layer(
            self.PERIODIC_TEST_RUNNER, layer, combine=True
        )

        # ensure the cron service is running
        self.container.start(self.service_name)

        logger.debug("Running tempest init script")
        try:
            self.execute(
                ["tempest-init"],
                user="tempest",
                group="tempest",
                working_dir=TEMPEST_HOME,
                exception_on_error=True,
                environment=env,
            )
        except ops.pebble.ExecError as e:
            if e.stdout:
                for line in e.stdout.splitlines():
                    logger.error("    %s", line)
            raise RuntimeError("tempest init failed")

    @assert_ready
    def run_tempest_tests(
        self,
        regexes: List[str],
        exclude_regex: str,
        test_list: str,
        serial: bool,
        env: Dict[str, str],
    ) -> str:
        """Wrapper for running a set of tempest tests.

        Return the output as a string.
        Raises a RuntimeError if something goes wrong.
        """
        # validation before running anything

        if not (regexes or exclude_regex or test_list):
            raise RuntimeError(
                "No filter parameters provided.\n"
                "At least one of regex, exclude-regex, or test-list must be provided to run tests.\n\n"
                "If you really intend to run all tests, pass regex='.*'.\n"
                "WARNING: the full test set is very large and will take a long time."
            )

        for r in [*regexes, exclude_regex]:
            try:
                re.compile(r)
            except re.error as e:
                raise RuntimeError(f"{r!r} is an invalid regex: {e}")

        if test_list and test_list not in self.get_test_lists():
            raise RuntimeError(
                f"'{test_list}' is not a known test list. "
                "Please run get-lists action to view available lists."
            )

        # now build the command line for tempest
        serial_args = ["--serial" if serial else "--parallel"]
        regex_args = ["--regex", " ".join(regexes)] if regexes else []
        exclude_regex_args = (
            ["--exclude-regex", exclude_regex] if exclude_regex else []
        )
        list_args = (
            ["--load-list", TEMPEST_LIST_DIR + "/" + test_list]
            if test_list
            else []
        )
        args = [
            "tempest-run-wrapper",
            *serial_args,
            *regex_args,
            *exclude_regex_args,
            *list_args,
        ]

        try:
            summary = self.execute(
                args,
                user="tempest",
                group="tempest",
                working_dir=TEMPEST_HOME,
                exception_on_error=True,
                environment=env,
            )
        except ops.pebble.ExecError:
            raise RuntimeError(
                "Error during test execution.\n"
                "For more information, copy log file from container by running:\n"
                + self.charm.get_copy_log_cmd()
            )

        return summary

    @assert_ready
    def run_extensive_cleanup(self, env: Dict[str, str]) -> None:
        """Wrapper for running extensive cleanup."""
        try:
            self.execute(
                ["python3", "cleanup.py", "extensive"],
                user="tempest",
                group="tempest",
                working_dir=TEMPEST_HOME,
                exception_on_error=True,
                environment=env,
            )
        except ops.pebble.ExecError:
            logger.warning("Clean-up failed")


@sunbeam_tracing.trace_type
class TempestUserIdentityRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for identity ops."""

    CREDENTIALS_SECRET_PREFIX = "tempest-user-identity-resource-"
    CONFIGURE_SECRET_PREFIX = "configure-credential-"

    teardown_ops = [
        {
            "name": "show_domain",
            "params": {
                "name": OPENSTACK_DOMAIN,
            },
        },
        {
            "name": "delete_project",
            "params": {
                "name": OPENSTACK_PROJECT,
                "domain": "{{ show_domain[0].id }}",
            },
        },
        {
            "name": "delete_user",
            "params": {
                "name": OPENSTACK_USER,
                "domain": "{{ show_domain[0].id }}",
            },
        },
        {
            "name": "update_domain",
            "params": {
                "domain": "{{ show_domain[0].id }}",
                "enable": False,
            },
        },
        {
            "name": "delete_domain",
            "params": {
                "name": "{{ show_domain[0].id }}",
            },
        },
    ]

    resource_identifiers: FrozenSet[str] = frozenset(
        {
            "name",
            "domain",
            "project",
        }
    )

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool,
        region: str,
    ):
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.charm = charm
        self.region = region

    @property
    def ready(self) -> bool:
        """Whether the relation is ready."""
        # We define that keystone relation is ready,
        # once we have all the responses to ops requests,
        # and the details have been stored
        # in the credentials secret maintained by the charm.
        content = self.get_user_credential()
        return bool(
            content and content.get("auth-url") and content.get("domain-id")
        )

    @property
    def label(self) -> str:
        """Secret label to share over keystone resource relation."""
        return self.CREDENTIALS_SECRET_PREFIX + OPENSTACK_USER

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for the relation."""
        import charms.keystone_k8s.v0.identity_resource as id_ops

        logger.debug("Setting up Identity Resource event handler")
        ops_svc = sunbeam_tracing.trace_type(id_ops.IdentityResourceRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ops_svc.on.provider_ready,
            self._on_provider_ready,
        )
        self.framework.observe(
            ops_svc.on.provider_goneaway,
            self._on_provider_goneaway,
        )
        self.framework.observe(
            ops_svc.on.response_available,
            self._on_response_available,
        )
        return ops_svc

    def get_user_credential(self) -> Optional[dict]:
        """Retrieve the user credential."""
        credentials_id = self.charm.leader_get(self.label)
        if not credentials_id:
            logger.warning("Failed to get openstack credential for tempest.")
            return None
        secret = self.model.get_secret(id=credentials_id)
        return secret.get_content(refresh=True)

    def _hash_ops(self, ops: list) -> str:
        """Hash ops request."""
        return hashlib.sha256(json.dumps(ops).encode()).hexdigest()

    def _ensure_credential(self) -> str:
        """Ensure the credential exists and return the secret id."""
        credentials_id = self.charm.leader_get(self.label)

        # If it exists and the credentials have already been set,
        # simply return the id
        if credentials_id:
            secret = self.model.get_secret(id=credentials_id)
            content = secret.get_content(refresh=True)
            if "password" in content:
                return credentials_id

        # Otherwise, generate and save the credentials.
        return self._set_secret(
            {
                "username": OPENSTACK_USER,
                "password": self._generate_password(18),
                "project-name": OPENSTACK_PROJECT,
                "domain-name": OPENSTACK_DOMAIN,
            },
        )

    def _set_secret(self, entries: Dict[str, str]) -> str:
        """Create or update a secret."""
        credential_id = self.charm.leader_get(self.label)

        # update secret if credential_id exists
        if credential_id:
            secret = self.model.get_secret(id=credential_id)
            content = secret.get_content(refresh=True)
            content.update(entries)
            if content != secret.get_content(refresh=True):
                secret.set_content(content)
            return credential_id

        # create new secret if credential_id does not exist
        credential_secret = self.model.app.add_secret(
            entries,
            label=self.label,
        )
        self.charm.leader_set({self.label: credential_secret.id})
        return credential_secret.id

    def _delete_secret(self):
        """Delete the credentials secret if exists.

        Is a no-op if the charm is not leader,
        because non-leader units cannot set application data.
        """
        if not self.model.unit.is_leader():
            return

        credential_id = self.charm.leader_get(self.label)
        if credential_id:
            secret = self.model.get_secret(id=credential_id)
            secret.remove_all_revisions()
            self.charm.leader_set({self.label: ""})

    def _generate_password(self, length: int) -> str:
        """Utility function to generate secure random string for password."""
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for i in range(length))

    def _grant_ops_secret(self, relation: ops.Relation) -> None:
        """Grant ops secret."""
        secret = self.model.get_secret(id=self._ensure_credential())
        secret.grant(relation)

    def _setup_tempest_resource_ops(self) -> List[dict]:
        """Set up openstack resource ops."""
        credential_id = self._ensure_credential()
        credential_secret = self.model.get_secret(id=credential_id)
        content = credential_secret.get_content(refresh=True)
        username = content.get("username")
        password = content.get("password")
        setup_ops = [
            {
                "name": "create_role",
                "params": {
                    "name": OPENSTACK_ROLE,
                },
            },
            {
                "name": "create_domain",
                "params": {
                    "name": OPENSTACK_DOMAIN,
                    "enable": True,
                },
            },
            {
                "name": "create_project",
                "params": {
                    "name": OPENSTACK_PROJECT,
                    "domain": "{{ create_domain[0].id }}",
                },
            },
            {
                "name": "create_user",
                "params": {
                    "name": username,
                    "password": password,
                    "domain": "{{ create_domain[0].id }}",
                },
            },
            {
                "name": "grant_role",
                "params": {
                    "role": "{{ create_role[0].id }}",
                    "domain": "{{ create_domain[0].id }}",
                    "user": "{{ create_user[0].id }}",
                    "user_domain": "{{ create_domain[0].id }}",
                },
            },
            {
                "name": "grant_role",
                "params": {
                    "role": "{{ create_role[0].id }}",
                    "user": "{{ create_user[0].id }}",
                    "user_domain": "{{ create_domain[0].id }}",
                    "project": "{{ create_project[0].id }}",
                    "project_domain": "{{ create_domain[0].id }}",
                },
            },
        ]
        return setup_ops

    def list_endpoint_ops(self) -> list[dict]:
        """Operations to list keystone endpoint."""
        return [
            {
                "name": "list_endpoint",
                "params": {
                    "name": "keystone",
                    "interface": "admin",
                    "region": self.region,
                },
            },
        ]

    def _setup_tempest_resource_request(self) -> dict:
        """Set up openstack resource for tempest."""
        ops = []
        # Teardown before setup to ensure it begins with a clean environment.
        ops.extend(self.teardown_ops)
        ops.extend(self._setup_tempest_resource_ops())
        ops.extend(self.list_endpoint_ops())
        request = {
            "id": self._hash_ops(ops),
            "tag": "setup_tempest_resource",
            "ops": ops,
        }
        return request

    def _process_list_endpoint_response(self, response: dict) -> None:
        """Process extra ops request: `_list_endpoint_ops`."""
        for op in response.get("ops", []):
            if op.get("name") != "list_endpoint":
                continue
            if op.get("return-code") != 0:
                logger.warning("List endpoint ops failed.")
                return
            for endpoint in op.get("value", {}):
                auth_url = endpoint.get("url")
                if auth_url is not None:
                    self._set_secret({"auth-url": auth_url})
                    return

    def _process_setup_tempest_resource_response(self, response: dict) -> None:
        """Process extra ops request: "_setup_tempest_resource_request"."""
        for op in response.get("ops", []):
            if op.get("name") != "create_domain":
                continue
            if op.get("return-code") != 0:
                logger.warning("Create domain ops failed.")
                return
            domain_id = op.get("value", {}).get("id")
            if domain_id is not None:
                self._set_secret({"domain-id": domain_id})
                return

    def _on_provider_ready(self, event) -> None:
        """Handles response available events."""
        if not self.model.unit.is_leader():
            return
        logger.info("Identity ops provider ready: setup tempest resources")
        self.interface.request_ops(self._setup_tempest_resource_request())
        self._grant_ops_secret(event.relation)

        # Mark tempest as not ready,
        # so that the tempest environment is definitely re-inited on rejoin.
        self.charm.set_tempest_ready(False)

        self.callback_f(event)

    def _on_response_available(self, event) -> None:
        """Handles response available events."""
        if not self.model.unit.is_leader():
            return
        logger.info("Handle response from identity ops")

        response = self.interface.response
        logger.info("%s", json.dumps(response, indent=4))
        self._process_list_endpoint_response(response)
        self._process_setup_tempest_resource_response(response)
        self.callback_f(event)

    def _on_provider_goneaway(self, event) -> None:
        """Handle gone_away event."""
        # If it's not the leader, skip these steps,
        # because the cleanup should only happen from a single leader unit.
        # Either way, multiple units are not supported or tested currently.
        if not self.model.unit.is_leader():
            return
        logger.info("Identity ops provider gone away")

        # If the relation is going away, then tempest is no longer ready,
        # and the environment should be inited again if rejoined.
        self.charm.set_tempest_ready(False)

        # Do an extensive clean-up upon identity relation removal if credential
        # exists.
        env = self.charm._get_cleanup_env()
        if env and env.get("OS_AUTH_URL"):
            pebble = self.charm.pebble_handler()
            pebble.run_extensive_cleanup(env)

        # Delete the stored keystone credentials,
        # because they are no longer valid.
        self._delete_secret()

        self.callback_f(event)


@sunbeam_tracing.trace_type
class GrafanaDashboardRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for grafana-dashboard relation."""

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for the relation."""
        logger.debug("Setting up Grafana Dashboards Provider event handler")
        interface = sunbeam_tracing.trace_type(
            grafana_dashboard.GrafanaDashboardProvider
        )(
            self.charm,
            relation_name=self.relation_name,
            dashboards_path="src/grafana_dashboards",
        )
        return interface

    @property
    def ready(self) -> bool:
        """Determine with the relation is ready for use."""
        return True


@sunbeam_tracing.trace_type
class LoggingRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for logging relation."""

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for the relation."""
        logger.debug("Setting up Logging Provider event handler")
        interface = sunbeam_tracing.trace_type(loki_push_api.LogProxyConsumer)(
            self.charm,
            recursive=True,
            relation_name=self.relation_name,
            alert_rules_path=ALERT_RULES_PATH,
            logs_scheme={
                "tempest": {
                    "log-files": [
                        TEMPEST_PERIODIC_OUTPUT,
                        TEMPEST_ADHOC_OUTPUT,
                    ]
                }
            },
        )

        self.framework.observe(
            interface.on.log_proxy_endpoint_joined,
            self._on_log_proxy_endpoint_changed,
        )
        self.framework.observe(
            interface.on.log_proxy_endpoint_departed,
            self._on_log_proxy_endpoint_changed,
        )

        return interface

    def _on_log_proxy_endpoint_changed(self, event):
        if not self.model.unit.is_leader():
            return

        # to trigger context re-rendering
        self.charm.configure_charm(event)

    @property
    def ready(self) -> bool:
        """Determine if the relation is ready for use."""
        try:
            logger.info("Checking logging relation readiness...")
            return bool(
                self.interface._promtail_config("tempest").get("clients", [])
            )
        except Exception as e:
            logger.warning("Error getting loki client endpoints. %s", str(e))
            return False
