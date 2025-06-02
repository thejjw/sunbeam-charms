#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
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

"""Gnocchi Operator Charm.

This charm provide Gnocchi services as part of an OpenStack deployment
"""

import logging
from typing import (
    List,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from charms.gnocchi_k8s.v0.gnocchi_service import (
    GnocchiServiceProvides,
    GnocchiServiceReadinessRequestEvent,
)
from ops.charm import (
    RelationEvent,
)
from ops.framework import (
    EventBase,
    StoredState,
)

logger = logging.getLogger(__name__)

GNOCHHI_WSGI_CONTAINER = "gnocchi-api"
GNOCCHI_METRICD_CONTAINER = "gnocchi-metricd"


@sunbeam_tracing.trace_type
class GnocchiServiceProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for Gnocchi service relation on provider side."""

    def setup_event_handler(self):
        """Configure event handlers for Gnocchi service relation."""
        logger.debug("Setting up Gnocchi service event handler")
        svc = sunbeam_tracing.trace_type(GnocchiServiceProvides)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.service_readiness,
            self._on_service_readiness,
        )
        return svc

    def _on_service_readiness(
        self, event: GnocchiServiceReadinessRequestEvent
    ) -> None:
        """Handle service readiness request event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


@sunbeam_tracing.trace_type
class GnocchiWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for Gnocchi WSGI services."""

    def init_service(self, context) -> None:
        """Enable and start WSGI service."""
        self.write_config(context)
        try:
            self.execute(["a2dissite", "gnocchi-api"], exception_on_error=True)
            self.execute(
                ["a2ensite", self.wsgi_service_name], exception_on_error=True
            )
        except ops.pebble.ExecError:
            logger.exception(
                f"Failed to enable {self.wsgi_service_name} site in apache"
            )
            # ignore for now - pebble is raising an exited too quickly, but it
            # appears to work properly.
        self.start_wsgi()

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfigs = super().default_container_configs()
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/gnocchi/api-paste.ini",
                    self.charm.service_user,
                    self.charm.service_group,
                    0o640,
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/gnocchi/api_audit_map.conf",
                    self.charm.service_user,
                    self.charm.service_group,
                    0o640,
                ),
            ]
        )
        _cconfigs.extend(self.charm.default_container_configs())
        return _cconfigs


@sunbeam_tracing.trace_type
class GnocchiMetricdPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Gnocchi metricd container."""

    def get_layer(self):
        """Gnocchi Metricd service.

        :returns: pebble service layer configuration for gnocchi metricd
                  service
        :rtype: dict
        """
        return {
            "summary": "gnocchi metricd layer",
            "description": "pebble configuration for gnocchi metricd service",
            "services": {
                "gnocchi-metricd": {
                    "override": "replace",
                    "summary": "Gnocchi Metricd",
                    "command": "gnocchi-metricd",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfigs = super().default_container_configs()
        _cconfigs.extend(self.charm.default_container_configs())
        return _cconfigs


class GnocchiOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "gnocchi-api"
    wsgi_admin_script = "/usr/bin/gnocchi-api"
    wsgi_public_script = "/usr/bin/gnocchi-api"

    db_sync_cmds = [["gnocchi-upgrade"]]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/gnocchi/gnocchi.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "gnocchi"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "gnocchi"

    @property
    def service_endpoints(self):
        """Return service endpoints for the service."""
        return [
            {
                "service_name": "gnocchi",
                "type": "metric",
                "description": "OpenStack Gnocchi API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 8041

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

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.svc_ready_handler = GnocchiServiceProvidesHandler(
            self,
            "gnocchi-service",
            self.handle_readiness_request_from_event,
        )
        handlers.append(self.svc_ready_handler)

        return handlers

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = [
            GnocchiWSGIPebbleHandler(
                self,
                GNOCHHI_WSGI_CONTAINER,
                self.service_name,
                [],
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            GnocchiMetricdPebbleHandler(
                self,
                GNOCCHI_METRICD_CONTAINER,
                "gnocchi-metricd",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        # Update with configs that are common for all containers
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/gnocchi/gnocchi.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                self.service_group,
                0o640,
            ),
        ]

    def configure_app_leader(self, event: EventBase):
        """Run global app setup.

        These are tasks that should only be run once per application and only
        the leader runs them.
        """
        super().configure_app_leader(event)
        self.set_readiness_on_related_units()

    def handle_readiness_request_from_event(
        self, event: RelationEvent
    ) -> None:
        """Set service readiness in relation data."""
        self.svc_ready_handler.interface.set_service_status(
            event.relation, self.bootstrapped()
        )

    def set_readiness_on_related_units(self) -> None:
        """Set service readiness on gnocchi-service related units."""
        logger.debug(
            "Set service readiness on all connected gnocchi-service relations"
        )
        for relation in self.framework.model.relations["gnocchi-service"]:
            self.svc_ready_handler.interface.set_service_status(relation, True)


@sunbeam_tracing.trace_sunbeam_charm
class GnocchiCephOperatorCharm(GnocchiOperatorCharm):
    """Charm the Gnocchi service with Ceph backend."""

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            sunbeam_ctxts.CephConfigurationContext(self, "ceph_config")
        )
        return contexts

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.ceph = sunbeam_rhandlers.CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name="rbd",
            mandatory="ceph" in self.mandatory_relations,
        )
        handlers.append(self.ceph)
        return handlers

    def configure_containers(self):
        """Setp ceph keyring and init pebble handlers that are ready."""
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                ph.execute(
                    [
                        "ceph-authtool",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "--create-keyring",
                        f"--name=client.{self.app.name}",
                        f"--add-key={self.ceph.key}",
                    ],
                    exception_on_error=True,
                )
                ph.execute(
                    [
                        "chown",
                        f"{self.service_user}:{self.service_group}",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "/etc/ceph/rbdmap",
                    ],
                    exception_on_error=True,
                )
                ph.execute(
                    [
                        "chmod",
                        "640",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "/etc/ceph/rbdmap",
                    ],
                    exception_on_error=True,
                )
                ph.configure_container(self.contexts())
            else:
                logging.debug(
                    f"Not running configure containers for {ph.service_name},"
                    " container not ready"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Payload container not ready"
                )
        super().configure_containers()

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfigs = super().default_container_configs()
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/ceph/ceph.conf",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
            ]
        )
        return _cconfigs


if __name__ == "__main__":  # pragma: nocover
    ops.main(GnocchiCephOperatorCharm)
