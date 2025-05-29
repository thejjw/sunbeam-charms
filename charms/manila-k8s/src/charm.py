#!/usr/bin/env python3
#
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

"""Manila Operator Charm.

This charm provides Manila services as part of an OpenStack deployment.
"""

import logging
from typing import (
    Callable,
    Dict,
    List,
    Mapping,
)

import charms.manila_k8s.v0.manila as manila_k8s
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

MANILA_API_PORT = 8786
MANILA_API_CONTAINER = "manila-api"
MANILA_SCHEDULER_CONTAINER = "manila-scheduler"
MANILA_RELATION_NAME = "manila"


@sunbeam_tracing.trace_type
class ManilaConfigurationContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set manila parameters."""

    def context(self) -> dict:
        """Generate configuration information for manila config."""
        share_protocols = self.charm.manila_share.interface.share_protocols
        ctxt = {
            "enabled_share_protocols": ",".join(share_protocols),
        }

        return ctxt


@sunbeam_tracing.trace_type
class ManilaSchedulerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Manila Scheduler."""

    def get_layer(self) -> dict:
        """Manila Scheduler service layer.

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "manila scheduler layer",
            "description": "pebble configuration for manila services",
            "services": {
                "manila-scheduler": {
                    "override": "replace",
                    "summary": "Manila Scheduler",
                    "command": "manila-scheduler",
                    "user": "manila",
                    "group": "manila",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/manila.conf",
                "root",
                "manila",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "manila",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
class ManilaRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handles the manila relation on the requires side."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        region: str,
        callback_f: Callable,
    ):
        """Constructor for ManilaRequiresHandler.

        Creates a new ManilaRequiresHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param region: the region the manila services are configured for
        :type region: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        """
        super().__init__(charm, relation_name, callback_f, True)
        self.region = region

    def setup_event_handler(self):
        """Configure event handlers for the manila service relation."""
        logger.debug("Setting up manila event handler")
        manila_handler = sunbeam_tracing.trace_type(manila_k8s.ManilaRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            manila_handler.on.manila_connected,
            self._manila_connected,
        )
        self.framework.observe(
            manila_handler.on.manila_goneaway,
            self._manila_goneaway,
        )
        return manila_handler

    def _manila_connected(self, event) -> None:
        """Handles manila connected events."""
        self.callback_f(event)

    def _manila_goneaway(self, event) -> None:
        """Handles manila goneaway events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Interface ready for use."""
        relations = self.model.relations[self.relation_name]

        # We need at least one relation.
        if not relations:
            return False

        for relation in relations:
            # All relations should have their data set.
            if not relation.data[relation.app].get(manila_k8s.SHARE_PROTOCOL):
                return False

        return True


@sunbeam_tracing.trace_sunbeam_charm
class ManilaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    service_name = "manila-api"
    wsgi_admin_script = "/usr/bin/manila-wsgi"
    wsgi_public_script = "/usr/bin/manila-wsgi"

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "manila",
            "manila-manage",
            "db",
            "sync",
        ],
    ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/manila/manila.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "manila"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "manila"

    @property
    def service_endpoints(self) -> List[Dict]:
        """Service endpoints for the Manila API services."""
        return [
            {
                "service_name": "manilav2",
                "type": "sharev2",
                "description": "OpenStack Shared File Systems V2",
                "internal_url": f"{self.internal_url}/v2",
                "public_url": f"{self.public_url}/v2",
                "admin_url": f"{self.admin_url}/v2",
            },
        ]

    @property
    def default_public_ingress_port(self):
        """Public ingress port for service."""
        return MANILA_API_PORT

    @property
    def ingress_healthcheck_path(self):
        """Healthcheck path for ingress relation."""
        return "/healthcheck"

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return (
            f"http://localhost:{self.default_public_ingress_port}/healthcheck"
        )

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for manila services."""
        return {"database": "manila"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                MANILA_API_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            ManilaSchedulerPebbleHandler(
                self,
                MANILA_SCHEDULER_CONTAINER,
                "manila-scheduler",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the operator."""
        handlers = super().get_relation_handlers(handlers or [])
        if self.can_add_handler(MANILA_RELATION_NAME, handlers):
            self.manila_share = ManilaRequiresHandler(
                self,
                MANILA_RELATION_NAME,
                self.model.config["region"],
                self.configure_charm,
            )
            handlers.append(self.manila_share)

        return handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(ManilaConfigurationContext(self, "manila_config"))
        return contexts

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/manila.conf",
                "root",
                "manila",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/manila/api-paste.ini",
                "root",
                "manila",
                0o640,
            ),
        ]
        return _cconfigs

    @property
    def db_sync_container_name(self) -> str:
        """Name of Container to run db sync from."""
        return MANILA_SCHEDULER_CONTAINER


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaOperatorCharm)
