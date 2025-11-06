#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
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

"""Horizon Operator Charm.

This charm provide Horizon services as part of an OpenStack
deployment
"""

import json
import logging
from typing import (
    List,
    Mapping,
)
from urllib import (
    parse,
)

import charms.keystone_k8s.v0.identity_endpoints as identity_endpoints
import ops
import ops.framework
import ops.model
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

HORIZON = "horizon"
TRUSTED_DASHBOARD_RELATION_NAME = "trusted-dashboard"


def exec(container: ops.model.Container, cmd: str):
    """Execute a command in a container."""
    logging.debug(f"Executing command: {cmd!r}")
    try:
        process = container.exec(cmd.split(), timeout=5 * 60)
        out, warnings = process.wait_output()
        if warnings:
            for line in warnings.splitlines():
                logger.warning(f"{cmd} warn: {line.strip()}")
        logging.debug(f"Output from {cmd!r}: \n{out}")
    except ops.pebble.ExecError:
        logger.exception(f"Command {cmd!r} failed")


def manage_plugins(
    container: ops.model.Container, plugins: List[str], enable: bool
) -> bool:
    """Enable or disable plugins based on enable flag.

    Return if any changes were made.
    """
    command = "enable" if enable else "disable"
    cmd = [
        "/usr/bin/plugin_management.py",
        command,
    ] + plugins
    logger.debug("%s plugins: %r", command, plugins)
    try:
        process = container.exec(cmd, timeout=1 * 60)
        out, err = process.wait_output()
    except ops.pebble.ExecError as e:
        logger.debug(
            "Error using %r on plugins: %r", command, plugins, exc_info=True
        )
        raise sunbeam_guard.BlockedExceptionError(
            f"Error using {command!r} on plugins: {plugins!r}"
        ) from e
    if err:
        logger.warning("Warning when using %r on plugins: %s", command, err)
    tag = "Enabled" if enable else "Disabled"
    return tag in out


def _remove_redundant_port(url: str) -> str:
    """Remove redundant port from URL if it matches the default for the scheme."""
    parsed = parse.urlparse(url)
    scheme_to_port = {
        "http": 80,
        "https": 443,
    }
    port = scheme_to_port.get(parsed.scheme, None)
    if port and port == parsed.port:
        return parse.urlunparse(
            (
                parsed.scheme,
                parsed.hostname,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
    return url


@sunbeam_tracing.trace_type
class AdditionalConfigAdapter(sunbeam_contexts.ConfigContext):
    """Configuration context for additional settings."""

    def context(self):
        """Configuration context."""
        parsed_pub_url = parse.urlparse(
            _remove_redundant_port(self.charm.public_url)
        )
        parsed_int_url = parse.urlparse(
            _remove_redundant_port(self.charm.internal_url)
        )

        regions = set()
        endpoints = self.charm.id_endpoints.interface.endpoints
        for endpoint in endpoints:
            region = endpoint.get("region")
            if region:
                regions.add(region)

        return {
            "public_endpoint": f"{parsed_pub_url.scheme}://{parsed_pub_url.netloc}",
            "internal_endpoint": f"{parsed_int_url.scheme}://{parsed_int_url.netloc}",
            "ssl_enabled": parsed_pub_url.scheme == "https",
            "regions": list(regions),
        }


@sunbeam_tracing.trace_type
class WSGIHorizonPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Horizon Pebble Handler."""

    charm: "HorizonOperatorCharm"

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        exec(container, "a2dissite 000-default")
        exec(container, "a2disconf openstack-dashboard")
        exec(container, "a2disconf other-vhosts-access-log")
        super().init_service(context)

    def files_changed(self, files: list[str]):
        """Call django utilities when local_settings.py changes."""
        logger.debug("Files changed: %r", files)
        if (
            self.charm.service_conf in files
            and self.charm.ingress_internal.ready
        ):
            logger.debug("local_settings.py changed, running django utilities")
            container = self.charm.unit.get_container(self.container_name)
            manage = "/usr/share/openstack-dashboard/manage.py"
            exec(
                container,
                manage + " collectstatic --no-input",
            )
            exec(
                container,
                manage + " compress --force",
            )


@sunbeam_tracing.trace_sunbeam_charm
class HorizonOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "horizon"
    wsgi_admin_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi"
    )
    wsgi_public_script = (
        "/usr/share/openstack-dashboard/openstack_dashboard/wsgi/django.wsgi"
    )

    db_sync_cmds = [
        [
            "python3",
            "/usr/share/openstack-dashboard/manage.py",
            "migrate",
            "--noinput",
        ]
    ]

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(plugins=[])
        self.framework.observe(
            self.on.get_dashboard_url_action,
            self._get_dashboard_url_action,
        )

    def _get_dashboard_url_action(self, event):
        """Retrieve the URL for the Horizon OpenStack Dashboard."""
        event.set_results({"url": self.public_url})

    @property
    def _websso_url(self) -> str:
        # remove redundant port if it exists. If we include the port,
        # keystone will not correctly match the dashboard URL to what is
        # configured in the [federation]/trusted_dashboard setting, making
        # authentication fail. The port is still needed if horizon is running
        # on a non-standard port.
        url = _remove_redundant_port(self.public_url)
        return url.rstrip("/") + "/auth/websso/"

    def _on_trusted_dashboard_providers_changed(self, event):
        """Handle changes in trusted dashboard providers."""
        if not self.model.unit.is_leader():
            return

        # Set the trusted dashboard URL regardless of whether or not the
        # requirer sets FID providers.
        self.trusted_dashboard.set_provider_info(
            trusted_dashboard=self._websso_url
        )

    @property
    def federated_providers(self) -> List[Mapping[str, str]]:
        """List of federated identity providers."""
        return self.trusted_dashboard.federated_providers

    @property
    def default_public_ingress_port(self):
        """Default public ingress port."""
        return 80

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/openstack-dashboard/local_settings.py"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "horizon"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "horizon"

    @property
    def service_endpoints(self):
        """Endpoints for horizon."""
        return [
            {
                "service_name": self.service_name,
                "type": "openstack-dashboard",
                "description": "OpenStack Horizon",
                "internal_url": self.internal_url,
                "public_url": self.public_url,
                "admin_url": self.admin_url,
            }
        ]

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/usr/local/share/ca-certificates/ca-bundle.pem",
                    "root",
                    self.service_group,
                    0o640,
                ),
            ]
        )
        return _cconfigs

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            WSGIHorizonPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Configure charm services."""
        super().configure_charm(event)
        if self.bootstrapped():
            # Handle the case where TLS is enabled/external hostname is changed
            # and we need to update the trusted dashboard URL in keystone.
            self._on_trusted_dashboard_providers_changed(event)
            self.status.set(ops.model.ActiveStatus(""))
            if self.model.unit.is_leader():
                if self.ingress_public.url:
                    self.model.app.status = ops.model.ActiveStatus(
                        self.ingress_public.url
                    )
                elif self.ingress_internal.url:
                    self.model.app.status = ops.model.ActiveStatus(
                        self.ingress_internal.url
                    )
                else:
                    self.model.app.status = ops.model.ActiveStatus()

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.configure_containers()
        self.run_db_sync()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        self.configure_plugins(event)
        self._state.unit_bootstrapped = True

    def configure_plugins(self, event: ops.framework.EventBase) -> None:
        """Configure plugins for horizon."""
        plugins = sorted(json.loads(self.config.get("plugins", "[]")))
        container = self.model.unit.get_container(self.service_name)
        if not container.can_connect():
            logger.debug("Container not ready, skipping plugin configuration")
            return

        old_plugins: List[str] = self._state.plugins  # type: ignore
        disabled_plugins = list(set(old_plugins) - set(plugins))
        any_changes = False
        if disabled_plugins:
            any_changes |= manage_plugins(container, disabled_plugins, False)
        if plugins:
            any_changes |= manage_plugins(container, plugins, True)
        self._state.plugins = plugins
        if any_changes:
            container.restart("wsgi-" + self.service_name)

    def _on_trusted_dashboard(self, event):
        self._on_trusted_dashboard_providers_changed(event)
        self.configure_unit(event)

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Get relation handlers for the charm."""
        handlers = handlers or []
        if self.can_add_handler(TRUSTED_DASHBOARD_RELATION_NAME, handlers):
            self.trusted_dashboard = (
                sunbeam_rhandlers.TrustedDashboardProvidesHandler(
                    self,
                    TRUSTED_DASHBOARD_RELATION_NAME,
                    self._on_trusted_dashboard,
                )
            )
            handlers.append(self.trusted_dashboard)

        if self.can_add_handler("identity-endpoints", handlers):
            self.id_endpoints = (
                sunbeam_rhandlers.IdentityEndpointsRequiresHandler(
                    self,
                    "identity-endpoints",
                    self.handle_keystone_endpoints,
                    mandatory="identity-endpoints" in self.mandatory_relations,
                )
            )
            handlers.append(self.id_endpoints)

        return super().get_relation_handlers(handlers)

    def handle_keystone_endpoints(self, event: ops.EventBase) -> None:
        """Event handler for identity ops."""
        if isinstance(
            event, identity_endpoints.IdentityEndpointsChangedEvent
        ) or isinstance(
            event, identity_endpoints.IdentityEndpointsGoneAwayEvent
        ):
            self.configure_charm(event)

    @property
    def healthcheck_period(self) -> str:
        """Healthcheck period for horizon service."""
        return "45s"

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return (
            super().healthcheck_http_url
            + self.ingress_internal.context().get(
                "ingress_path", self.model.name + "-horizon"
            )
            + "/auth/login/"
        )

    @property
    def healthcheck_http_timeout(self) -> str:
        """Healthcheck HTTP check timeout for the service."""
        return "30s"

    @property
    def config_contexts(self) -> List[sunbeam_contexts.ConfigContext]:
        """Configuration adapters for the operator."""
        contexts = super().config_contexts
        contexts.extend(
            [
                AdditionalConfigAdapter(self, "extra_config"),
            ]
        )
        return contexts


if __name__ == "__main__":  # pragma: nocover
    ops.main(HorizonOperatorCharm)
