# Copyright 2021 Canonical Ltd.
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

"""Base classes for defining Pebble handlers.

The PebbleHandler defines the pebble layers, manages pushing
configuration to the containers and managing the service running
in the container.
"""

import collections
import logging
import typing
from collections.abc import (
    Callable,
)

import ops.charm
import ops.pebble
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.templating as sunbeam_templating
import ops_sunbeam.tracing as sunbeam_tracing
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    WaitingStatus,
)

if typing.TYPE_CHECKING:
    from ops_sunbeam.charm import (
        OSBaseOperatorAPICharm,
        OSBaseOperatorCharm,
    )

logger = logging.getLogger(__name__)

ContainerDir = collections.namedtuple(
    "ContainerDir", ["path", "user", "group"]
)


@sunbeam_tracing.trace_type
class PebbleHandler(ops.framework.Object, metaclass=sunbeam_core.PostInitMeta):
    """Base handler for Pebble based containers."""

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        container_name: str,
        service_name: str,
        container_configs: list[sunbeam_core.ContainerConfigFile],
        template_dir: str,
        callback_f: Callable,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, None)
        self.charm = charm
        self.container_name = container_name
        self.service_name = service_name
        self.container_configs = container_configs
        self.container_configs.extend(self.default_container_configs())
        self.template_dir = template_dir
        self.callback_f = callback_f

        self.status = compound_status.Status("container:" + container_name)
        self.charm.status_pool.add(self.status)

        self.framework.observe(
            self.charm.on.update_status, self._on_update_status
        )
        self._files_changed: list[str] = []

    def __post_init__(self) -> None:
        """Post init."""
        self.setup_pebble_handler()

    def setup_pebble_handler(self) -> None:
        """Configure handler for pebble ready event."""
        prefix = self.container_name.replace("-", "_")
        pebble_ready_event = getattr(self.charm.on, f"{prefix}_pebble_ready")
        pebble_check_failed = getattr(
            self.charm.on, f"{prefix}_pebble_check_failed"
        )
        pebble_check_recovered = getattr(
            self.charm.on, f"{prefix}_pebble_check_recovered"
        )
        self.framework.observe(
            pebble_ready_event, self._on_service_pebble_ready
        )
        self.framework.observe(
            pebble_check_failed, self._on_pebble_check_failed
        )
        self.framework.observe(
            pebble_check_recovered, self._on_pebble_check_recovered
        )

    def _on_service_pebble_ready(
        self, event: ops.charm.PebbleReadyEvent
    ) -> None:
        """Handle pebble ready event."""
        container = event.workload
        container.add_layer(self.service_name, self.get_layer(), combine=True)
        self.charm.configure_charm(event)

    def _on_pebble_check_failed(
        self, event: ops.charm.PebbleCheckFailedEvent
    ) -> None:
        """Handle pebble check failed event."""
        self._on_update_status(event)

    def _on_pebble_check_recovered(
        self, event: ops.charm.PebbleCheckRecoveredEvent
    ) -> None:
        """Handle pebble check recovered event."""
        self._on_update_status(event)

    def write_config(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> list[str]:
        """Write configuration files into the container.

        Write self.container_configs into container if there contents
        have changed.

        :return: List of files that were updated
        :rtype: List
        """
        files_updated = []
        container = self.charm.unit.get_container(self.container_name)
        if container:
            for config in self.container_configs:
                changed = sunbeam_templating.sidecar_config_render(
                    container,
                    config,
                    self.template_dir,
                    context,
                )
                if changed:
                    files_updated.append(config.path)
                    logger.debug(f"Changes detected in {files_updated}")
                else:
                    logger.debug("No file changes detected")
        else:
            logger.debug("Container not ready")
        if files_updated:
            logger.debug(f"Changes detected in {files_updated}")
        else:
            logger.debug("No file changes detected")
        return files_updated

    def get_layer(self) -> ops.pebble.LayerDict:
        """Pebble configuration layer for the container."""
        return {}

    def get_healthcheck_layer(self) -> ops.pebble.LayerDict:
        """Pebble configuration for health check layer for the container."""
        return {}

    @property
    def directories(self) -> list[ContainerDir]:
        """List of directories to create in container."""
        return []

    def setup_dirs(self) -> None:
        """Create directories in container."""
        if self.directories:
            container = self.charm.unit.get_container(self.container_name)
            for d in self.directories:
                logging.debug(f"Creating {d.path}")
                container.make_dir(
                    d.path, user=d.user, group=d.group, make_parents=True
                )

    def _reset_files_changed(self) -> None:
        """Reset list of files changed."""
        self._files_changed = []

    def configure_container(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> None:
        """Write configuration files to the container."""
        self.setup_dirs()
        self._files_changed.extend(self.write_config(context))
        self.files_changed(self._files_changed)

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Initialise service ready for use.

        Record that service is ready for us.
        """
        self.configure_container(context)
        self.status.set(ActiveStatus(""))

    def default_container_configs(
        self,
    ) -> list[sunbeam_core.ContainerConfigFile]:
        """Generate default container configurations.

        These should be used by all inheriting classes and are
        automatically added to the list or container configurations
        provided during object instantiation.
        """
        return []

    @property
    def pebble_ready(self) -> bool:
        """Determine if pebble is running and ready for use."""
        return self.charm.unit.get_container(self.container_name).can_connect()

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if not self.pebble_ready:
            return False
        container = self.charm.unit.get_container(self.container_name)
        services = container.get_services()
        return all(s.is_running() for s in services.values())

    def execute(
        self, cmd: list[str], exception_on_error: bool = False, **kwargs
    ) -> str:
        """Execute given command in container managed by this handler.

        :param cmd: command to execute, specified as a list of strings
        :param exception_on_error: determines whether or not to raise
            an exception if the command fails. By default, this method
            will not raise an exception if the command fails. If it is
            raised, this will rase an ops.pebble.ExecError.
        :param kwargs: arguments to pass into the ops.model.Container's
            execute command.
        """
        container = self.charm.unit.get_container(self.container_name)
        process = container.exec(cmd, **kwargs)
        try:
            stdout, _ = process.wait_output()
            # Not logging the command in case it included a password,
            # too cautious ?
            logger.debug("Command complete")
            if stdout:
                for line in stdout.splitlines():
                    logger.debug("    %s", line)
            return stdout
        except ops.pebble.ExecError as e:
            logger.error("Exited with code %d. Stderr:", e.exit_code)
            if e.stderr:
                for line in e.stderr.splitlines():
                    logger.error("    %s", line)
            if exception_on_error:
                raise
        return ""

    def add_healthchecks(self) -> None:
        """Add healthcheck layer to the plan."""
        healthcheck_layer = self.get_healthcheck_layer()
        if not healthcheck_layer:
            logger.debug("Healthcheck layer not defined in pebble handler")
            return

        container = self.charm.unit.get_container(self.container_name)
        try:
            plan = container.get_plan()
            if not plan.checks:
                logger.debug("Adding healthcheck layer to the plan")
                container.add_layer(
                    "healthchecks", healthcheck_layer, combine=True
                )
        except ops.pebble.ConnectionError as connect_error:
            logger.error("Not able to add Healthcheck layer")
            logger.exception(connect_error)

    def stop_healthcheck(self, check_name: str) -> None:
        """Stop a healthcheck."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            plan = container.get_plan()
            if plan.checks and check_name in plan.checks:
                logger.debug(f"Stopping healthcheck for {check_name}")
                container.stop_checks("up")

        except ops.pebble.ConnectionError as connect_error:
            logger.error("Not able to stop the %s healthcheck", check_name)
            logger.exception(connect_error)

    def start_healthcheck(self, check_name: str) -> None:
        """Start a healthcheck."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            plan = container.get_plan()
            if plan.checks and check_name in plan.checks:
                logger.debug(f"Starting healthcheck for {check_name}")
                container.start_checks("up")

        except ops.pebble.ConnectionError as connect_error:
            logger.error("Not able to start the %s healthcheck", check_name)
            logger.exception(connect_error)

    def _on_update_status(self, event: ops.framework.EventBase) -> None:
        """Assess and set status.

        Also takes into account healthchecks.
        """
        if self.charm.is_service_paused:
            logger.warning(
                "Unit is paused, not checking container status for %s",
                self.container_name,
            )
            return
        if not self.pebble_ready:
            self.status.set(WaitingStatus("pebble not ready"))
            return

        if not self.service_ready:
            self.status.set(WaitingStatus("service not ready"))
            return

        failed = []
        container = self.charm.unit.get_container(self.container_name)
        checks = container.get_checks(level=ops.pebble.CheckLevel.READY)
        for name, check in checks.items():
            if check.status != ops.pebble.CheckStatus.UP:
                failed.append(name)

        # Verify alive checks if ready checks are missing
        if not checks:
            checks = container.get_checks(level=ops.pebble.CheckLevel.ALIVE)
            for name, check in checks.items():
                if check.status != ops.pebble.CheckStatus.UP:
                    failed.append(name)

        if failed:
            self.status.set(
                BlockedStatus(
                    "healthcheck{} failed: {}".format(
                        "s" if len(failed) > 1 else "", ", ".join(failed)
                    )
                )
            )
            return

        self.status.set(ActiveStatus(""))

    @staticmethod
    def _restart_service(container: ops.Container, service_name: str) -> None:
        """Restart service in container.

        :param container: Container to restart service in.
        :param service_name: Service to restart.
        """
        container.restart(service_name)

    @property
    def _restart_methods(
        self,
    ) -> typing.Mapping[str, Callable[[ops.Container, str], None]]:
        """Mapping of service names to restart methods."""
        return {}

    def start_all(
        self,
        restart: bool = True,
    ) -> None:
        """Start services in container.

        :param restart: Whether to stop services before starting them.
        """
        container = self.charm.unit.get_container(self.container_name)
        if not container.can_connect():
            logger.debug(
                f"Container {self.container_name} not ready, deferring restart"
            )
            return
        services = container.get_services()
        for service_name, service in services.items():
            if not service.is_running():
                logger.debug(
                    f"Starting {service_name} in {self.container_name}"
                )
                container.start(service_name)
                self._reset_files_changed()
                continue

            if restart:
                logger.debug(
                    f"Restarting {service_name} in {self.container_name}"
                )
                self._restart_methods.get(service_name, self._restart_service)(
                    container, service_name
                )
                self._reset_files_changed()

    def stop_all(self) -> None:
        """Stop services in container."""
        container = self.charm.unit.get_container(self.container_name)
        if not container.can_connect():
            logger.debug(
                f"Container {self.container_name} not ready, no need to stop"
            )
            return

        services = container.get_services()
        if services:
            logger.debug("Stopping all services")
            container.stop(*services.keys())

    def files_changed(self, files: list[str]):
        """Called when files have changed before restarting services."""


@sunbeam_tracing.trace_type
class ServicePebbleHandler(PebbleHandler):
    """Container handler for containers which manage a service."""

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Initialise service ready for use.

        Write configuration files to the container and record
        that service is ready for us.
        """
        self.configure_container(context)
        if self._files_changed:
            self.start_service(restart=True)
        else:
            self.start_service(restart=False)
        self.status.set(ActiveStatus(""))

    def start_service(self, restart: bool = True) -> None:
        """Check and start services in container.

        :param restart: Whether to stop services before starting them.
        """
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(
                f"{self.container_name} container is not ready. "
                "Cannot start service."
            )
            return
        if self.service_name not in container.get_services().keys():
            container.add_layer(
                self.service_name, self.get_layer(), combine=True
            )
        self.start_all(restart=restart)


@sunbeam_tracing.trace_type
class WSGIPebbleHandler(PebbleHandler):
    """WSGI oriented handler for a Pebble managed container."""

    charm: "OSBaseOperatorAPICharm"

    def __init__(
        self,
        charm: "OSBaseOperatorAPICharm",
        container_name: str,
        service_name: str,
        container_configs: list[sunbeam_core.ContainerConfigFile],
        template_dir: str,
        callback_f: Callable,
        wsgi_service_name: str,
    ) -> None:
        """Run constructor."""
        super().__init__(
            charm,
            container_name,
            service_name,
            container_configs,
            template_dir,
            callback_f,
        )
        self.wsgi_service_name = wsgi_service_name

    def start_wsgi(self, restart: bool = True) -> None:
        """Check and start services in container.

        :param restart: Whether to stop services before starting them.
        """
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(
                f"{self.container_name} container is not ready. "
                "Cannot start wgi service."
            )
            return
        if self.wsgi_service_name not in container.get_services().keys():
            container.add_layer(
                self.service_name, self.get_layer(), combine=True
            )
        self.start_all(restart=restart)

    def start_service(self) -> None:
        """Start the service."""
        self.start_wsgi()

    def get_layer(self) -> ops.pebble.LayerDict:
        """Apache WSGI service pebble layer.

        :returns: pebble layer configuration for wsgi service
        """
        return {
            "summary": f"{self.service_name} layer",
            "description": "pebble config layer for apache wsgi",
            "services": {
                f"{self.wsgi_service_name}": {
                    "override": "replace",
                    "summary": f"{self.service_name} wsgi",
                    "command": "/usr/sbin/apache2 -DFOREGROUND -DNO_DETACH",
                    "startup": "disabled",
                    "environment": {
                        "APACHE_RUN_DIR": "/var/run/apache2",
                        "APACHE_PID_FILE": "/var/run/apache2/apache2.pid",
                        "APACHE_LOCK_DIR": "/var/lock/apache2",
                        "APACHE_RUN_USER": "www-data",
                        "APACHE_RUN_GROUP": "www-data",
                        "APACHE_LOG_DIR": "/var/log/apache2",
                    },
                },
            },
        }

    def get_healthcheck_layer(self) -> ops.pebble.LayerDict:
        """Apache WSGI health check pebble layer.

        :returns: pebble health check layer configuration for wsgi service
        """
        return {
            "checks": {
                "up": {
                    "override": "replace",
                    "level": "alive",
                    "period": "10s",
                    "timeout": "3s",
                    "threshold": 3,
                    "exec": {"command": "service apache2 status"},
                },
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "period": self.charm.healthcheck_period,
                    "timeout": self.charm.healthcheck_http_timeout,
                    "http": {"url": self.charm.healthcheck_http_url},
                },
            }
        }

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        self.configure_container(context)
        try:
            process = container.exec(
                ["a2ensite", self.wsgi_service_name], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2ensite warn: %s", line.strip())
            logging.debug(f"Output from a2ensite: \n{out}")
        except ops.pebble.ExecError:
            logger.exception(
                f"Failed to enable {self.wsgi_service_name} site in apache"
            )
            # ignore for now - pebble is raising an exited too quickly, but it
            # appears to work properly.
        self._files_changed.extend(self.write_config(context))
        self.files_changed(self._files_changed)
        if self._files_changed:
            self.start_wsgi(restart=True)
        else:
            self.start_wsgi(restart=False)
        self.status.set(ActiveStatus(""))

    @property
    def wsgi_conf(self) -> str:
        """Location of WSGI config file."""
        return f"/etc/apache2/sites-available/wsgi-{self.service_name}.conf"

    def default_container_configs(
        self,
    ) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configs for WSGI service."""
        return [
            sunbeam_core.ContainerConfigFile(self.wsgi_conf, "root", "root")
        ]
