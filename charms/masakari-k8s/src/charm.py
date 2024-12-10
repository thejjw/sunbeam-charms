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
"""Masakari Operator Charm.

This charm provide Masakari services as part of an OpenStack deployment
"""

import logging
from collections import (
    OrderedDict,
)
from typing import (
    Callable,
)

import ops.framework
import ops.model
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import yaml
from charms.consul_k8s.v0.consul_cluster import (
    ConsulEndpointsRequirer,
)
from ops import (
    main,
)
from ops.charm import (
    RelationEvent,
)
from ops.model import (
    BlockedStatus,
)

logger = logging.getLogger(__name__)

MASAKARI_API_CONTAINER = "masakari-api"
MASAKARI_ENGINE_CONTAINER = "masakari-engine"
MASAKARI_HOSTMONITOR_CONTAINER = "masakari-hostmonitor"


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


@sunbeam_tracing.trace_type
class ConsulEndpointsRequirerHandler(sunbeam_rhandlers.RelationHandler):
    """Handle consul cluster relation on the requires side."""

    interface: "ConsulEndpointsRequirer"

    def __init__(
        self,
        charm: "sunbeam_charm.OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = False,
    ):
        """Create a new consul-cluster handler.

        Create a new ConsulEndpointsRequirerHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        :param mandatory: If the relation is mandatory to proceed with
                          configuring charm
        :type mandatory: bool
        """
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for consul-cluster relation."""
        logger.debug(f"Setting up {self.relation_name} event handler")
        svc = sunbeam_tracing.trace_type(ConsulEndpointsRequirer)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.endpoints_changed,
            self._on_consul_cluster_endpoints_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_consul_cluster_goneaway,
        )
        return svc

    def _on_consul_cluster_endpoints_changed(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle endpoints_changed  event."""
        logger.debug(
            f"Consul cluster endpoints changed event received for relation {self.relation_name}"
        )
        self.callback_f(event)

    def _on_consul_cluster_goneaway(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle gone_away  event."""
        logger.debug(
            f"Consul cluster gone away event received for relation {self.relation_name}"
        )
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return bool(self.interface.internal_http_endpoint)


@sunbeam_tracing.trace_type
class MasakariConfigurationContext(config_contexts.ConfigContext):
    """Configuration context for Masakar."""

    def construct_consul_matrix(self) -> str | None:
        """Construct Consul matrix yaml."""
        agent_handlers_map = OrderedDict(
            {
                "manage": self.charm.consul_management.ready,
                "tenant": self.charm.consul_tenant.ready,
                "storage": self.charm.consul_storage.ready,
            }
        )
        sequence = [k for k, v in agent_handlers_map.items() if v]
        active_agents_count = len(sequence)

        # Do not set any matrix if no agents are active
        # Leave it to DEFAULTS from masakarimonitors if all the consul agents are enabled
        # and so do not set any matrix
        # https://opendev.org/openstack/masakari-monitors/src/commit/21a78c65d3e0536500ab55a7868c1edb99131b67/masakarimonitors/hostmonitor/consul_check/matrix_helper.py#L26 # noqa
        if active_agents_count in {0, 3}:
            return None

        matrix = []
        if active_agents_count == 1:
            up = {"health": ["up"], "action": []}
            down = {"health": ["down"], "action": ["recovery"]}
            matrix.extend([up, down])
        elif active_agents_count == 2:
            # Defaults for 2*2 matrix with no actions
            up_up = {"health": ["up", "up"], "action": []}
            up_down = {"health": ["up", "down"], "action": []}
            down_up = {"health": ["down", "up"], "action": []}
            down_down = {"health": ["down", "down"], "action": []}

            # Actions should be recovery if storage is down
            # If storage not present, consider management handles storage as well
            if sequence == ["manage", "tenant"]:
                down_up["action"] = ["recovery"]
                down_down["action"] = ["recovery"]
            elif sequence == ["manage", "storage"]:
                up_down["action"] = ["recovery"]
                down_down["action"] = ["recovery"]
            elif sequence == ["tenant", "storage"]:
                up_down["action"] = ["recovery"]
                down_down["action"] = ["recovery"]

            matrix.extend([up_up, up_down, down_up, down_down])

        matrix_yaml = yaml.safe_dump({"sequence": sequence, "matrix": matrix})
        return matrix_yaml

    def context(self) -> dict:
        """Generate context information for masakari config."""
        ctx = {}
        matrix = self.construct_consul_matrix()
        if matrix:
            ctx["consul_matrix"] = matrix

        logger.debug(f"Context from masakari {ctx}")
        return ctx


@sunbeam_tracing.trace_type
class MasakariWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for Masakari API container."""

    charm: "MasakariOperatorCharm"

    def init_service(self, context: sunbeam_core.OPSCharmContexts):
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        exec(container, "a2dissite masakari-api")
        super().init_service(context)


@sunbeam_tracing.trace_type
class MasakariEnginePebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Masakari Engine container."""

    def get_layer(self):
        """Pebble layer for Masakari Engine service.

        :returns: pebble service layer config for masakari engine service
        :rtype: dict
        """
        return {
            "summary": "masakari engine layer",
            "description": "pebble configuration for masakari engine service",
            "services": {
                "masakari-engine": {
                    "override": "replace",
                    "summary": "masakari engine",
                    "command": "masakari-engine",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_type
class MasakariHostMonitorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Masakari Host monitor container."""

    def get_layer(self):
        """Pebble layer for Masakari Host monitor service.

        :returns: pebble service layer config for masakari host monitor service
        :rtype: dict
        """
        return {
            "summary": "masakari host monitor layer",
            "description": "pebble configuration for masakari host monitor service",
            "services": {
                "masakari-hostmonitor": {
                    "override": "replace",
                    "summary": "masakari host monitor",
                    "command": "masakari-hostmonitor --config-file /etc/masakari/masakarimonitors.conf",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    def default_container_configs(
        self,
    ) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/masakari/masakarimonitors.conf",
                self.charm.service_user,
                self.charm.service_group,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/masakari/matrix.yaml",
                self.charm.service_user,
                self.charm.service_group,
            ),
        ]


@sunbeam_tracing.trace_sunbeam_charm
class MasakariOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    wsgi_admin_script = "/usr/bin/masakari-wsgi"
    wsgi_public_script = "/usr/bin/masakari-wsgi"

    db_sync_cmds = [
        [
            "masakari-manage",
            "db",
            "sync",
        ]
    ]

    # Initialise custom event handlers
    consul_management: ConsulEndpointsRequirerHandler | None = None
    consul_tenant: ConsulEndpointsRequirerHandler | None = None
    consul_storage: ConsulEndpointsRequirerHandler | None = None

    @property
    def config_contexts(self) -> list[config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(MasakariConfigurationContext(self, "masakari_config"))
        return contexts

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    path="/usr/local/share/ca-certificates/ca-bundle.pem",
                    user="root",
                    group=self.service_group,
                    permissions=0o640,
                ),
            ]
        )
        return _cconfigs

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        self.consul_management = ConsulEndpointsRequirerHandler(
            self,
            "consul-management",
            self.configure_charm,
            "consul-management" in self.mandatory_relations,
        )
        handlers.append(self.consul_management)

        self.consul_tenant = ConsulEndpointsRequirerHandler(
            self,
            "consul-tenant",
            self.configure_charm,
            "consul-tenant" in self.mandatory_relations,
        )
        handlers.append(self.consul_tenant)

        self.consul_storage = ConsulEndpointsRequirerHandler(
            self,
            "consul-storage",
            self.configure_charm,
            "consul-storage" in self.mandatory_relations,
        )
        handlers.append(self.consul_storage)

        self.svc_ready_handler = (
            sunbeam_rhandlers.ServiceReadinessProviderHandler(
                self,
                "masakari-service",
                self.handle_readiness_request_from_event,
            )
        )
        handlers.append(self.svc_ready_handler)

        handlers = super().get_relation_handlers(handlers)
        return handlers

    def get_pebble_handlers(self):
        """Pebble handlers for operator."""
        pebble_handlers = []
        pebble_handlers.extend(
            [
                MasakariWSGIPebbleHandler(
                    self,
                    MASAKARI_API_CONTAINER,
                    self.service_name,
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                    f"wsgi-{self.service_name}",
                ),
                MasakariEnginePebbleHandler(
                    self,
                    MASAKARI_ENGINE_CONTAINER,
                    "masakari-engine",
                    self.container_configs,
                    self.template_dir,
                    self.configure_charm,
                ),
                MasakariHostMonitorPebbleHandler(
                    self,
                    MASAKARI_HOSTMONITOR_CONTAINER,
                    "masakari-hostmonitor",
                    [],
                    self.template_dir,
                    self.configure_charm,
                ),
            ]
        )
        return pebble_handlers

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        super().post_config_setup()
        self.set_readiness_on_related_units()

    def handle_readiness_request_from_event(
        self, event: RelationEvent
    ) -> None:
        """Set service readiness in relation data."""
        self.svc_ready_handler.interface.set_service_status(
            event.relation, self.bootstrapped()
        )

    def set_readiness_on_related_units(self) -> None:
        """Set service readiness on masakari-service related units."""
        logger.debug(
            "Set service readiness on all connected masakari-service relations"
        )
        for relation in self.framework.model.relations["masakari-service"]:
            self.svc_ready_handler.interface.set_service_status(relation, True)

    @property
    def service_name(self):
        """Service name."""
        return "masakari-api"

    @property
    def service_conf(self):
        """Service default configuration file."""
        return "/etc/masakari/masakari.conf"

    @property
    def service_user(self):
        """Service user file and directory ownership."""
        return "masakari"

    @property
    def service_group(self):
        """Service group file and directory ownership."""
        return "masakari"

    @property
    def service_endpoints(self):
        """Return masakari service endpoints."""
        return [
            {
                "service_name": "masakari",
                "type": "instance-ha",
                "description": "OpenStack Masakari API",
                "internal_url": f"{self.internal_url}/v1/$(tenant_id)s",
                "public_url": f"{self.public_url}/v1/$(tenant_id)s",
                "admin_url": f"{self.admin_url}/v1/$(tenant_id)s",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default port."""
        return 15868


if __name__ == "__main__":
    main(MasakariOperatorCharm)
