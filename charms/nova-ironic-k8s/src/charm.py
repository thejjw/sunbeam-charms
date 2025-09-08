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

"""nova-compute for Ironic Operator Charm.

This charm provides nova-compute service for Ironic as part of an OpenStack
deployment.
"""

import logging
import socket
from typing import (
    List,
    Mapping,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

NOVA_IRONIC_CONTAINER = "nova-ironic"
NOVA_NOVNCPROXY_INGRESS_NAME = "nova-novncproxy"


@sunbeam_tracing.trace_type
class NovaIronicPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for nova-ironic."""

    def get_layer(self) -> dict:
        """nova-ironic service layer.

        :returns: pebble layer configuration for the nova-ironic service
        :rtype: dict
        """
        return {
            "summary": "nova-ironic layer",
            "description": "pebble configuration for nova-ironic service",
            "services": {
                "nova-ironic": {
                    "override": "replace",
                    "summary": "Nova Compute for Ironic",
                    "command": "nova-compute --config-file /etc/nova/nova.conf",
                    "user": "nova",
                    "group": "nova",
                },
                "nova-novncproxy": {
                    "override": "replace",
                    "summary": "Nova noVNC Proxy for Ironic",
                    "command": "nova-novncproxy --config-file /etc/nova/nova.conf",
                    "user": "nova",
                    "group": "nova",
                },
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class NovaIronicOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "nova-ironic"

    def __init__(self, framework):
        super().__init__(framework)
        self.traefik_route_public = None
        self.traefik_route_internal = None
        self.framework.observe(
            self.on["peers"].relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.on["peers"].relation_departed, self._on_peer_relation_departed
        )

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "nova"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "nova"

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for nova-ironic services."""
        return {"database": "nova"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            NovaIronicPebbleHandler(
                self,
                NOVA_IRONIC_CONTAINER,
                NOVA_IRONIC_CONTAINER,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for operator."""
        handlers = super().get_relation_handlers(handlers or [])

        self.ironic_svc = sunbeam_rhandlers.ServiceReadinessRequiresHandler(
            self,
            "ironic-api",
            self.configure_charm,
            "ironic-api" in self.mandatory_relations,
        )
        handlers.append(self.ironic_svc)

        self.traefik_route_public = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-public",
            self.handle_traefik_public_ready,
            "traefik-route-public" in self.mandatory_relations,
            [NOVA_NOVNCPROXY_INGRESS_NAME],
        )
        handlers.append(self.traefik_route_public)

        self.traefik_route_internal = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-internal",
            self.handle_traefik_internal_ready,
            "traefik-route-internal" in self.mandatory_relations,
            [NOVA_NOVNCPROXY_INGRESS_NAME],
        )
        handlers.append(self.traefik_route_internal)

        return handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/rootwrap.conf",
                "root",
                "nova",
                0o640,
            ),
        ]
        return _cconfigs

    def _on_peer_relation_created(
        self, event: ops.framework.EventBase
    ) -> None:
        logger.info("Setting peer unit data")
        self.peers.set_unit_data({"host": socket.getfqdn()})

    def _on_peer_relation_departed(
        self, event: ops.framework.EventBase
    ) -> None:
        self.handle_traefik_ready(event)

    def handle_traefik_ready(self, event: ops.EventBase):
        """Handle Traefik route ready callback."""
        self.handle_traefik_public_ready(event)
        self.handle_traefik_internal_ready(event)

    def handle_traefik_public_ready(self, event: ops.EventBase):
        """Handle Traefik public route ready callback."""
        if not self.unit.is_leader():
            logger.debug(
                "Not a leader unit, skipping traefik public route config"
            )
            return

        traefik = self.traefik_route_public
        if traefik and traefik.interface.is_ready():
            logger.debug("Sending traefik config for public interface")
            traefik.interface.submit_to_traefik(config=self.traefik_config)

    def handle_traefik_internal_ready(self, event: ops.EventBase):
        """Handle Traefik internal route ready callback."""
        if not self.unit.is_leader():
            logger.debug(
                "Not a leader unit, skipping traefik internal route config"
            )
            return

        traefik = self.traefik_route_internal
        if traefik and traefik.interface.is_ready():
            logger.debug("Sending traefik config for internal interface")
            traefik.interface.submit_to_traefik(config=self.traefik_config)

            # traefik-route-internal is a mandatory relation. If this is the
            # last relation added, run configure_charm to potentially kick it
            # from the Waiting status.
            self.configure_charm(event)

    @property
    def traefik_config(self) -> dict:
        """Config to publish to traefik."""
        model = self.model.name
        router_cfg = {}
        # Add router for ironic-novncproxy
        # Rename router tls and add priority as workaround for
        # bug https://github.com/canonical/traefik-k8s-operator/issues/335
        router_cfg.update(
            {
                f"juju-{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}-router": {
                    "rule": f"PathPrefix(`/{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}`)",
                    "middlewares": [
                        "custom-stripprefix",
                        "custom-wsheaders-http",
                    ],
                    "service": f"juju-{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}-service",
                    "entryPoints": ["web"],
                },
                f"juju-{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}-router-https": {
                    "rule": f"PathPrefix(`/{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}`)",
                    "middlewares": [
                        "custom-stripprefix",
                        "custom-wsheaders-https",
                    ],
                    "service": f"juju-{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}-service",
                    "entryPoints": ["websecure"],
                    "tls": {},
                    "priority": 100,
                },
            }
        )

        # Add middlewares to nova-novncproxy
        middleware_cfg = {
            "custom-stripprefix": {
                "stripPrefix": {
                    "prefixes": [f"/{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}"],
                    "forceSlash": False,
                }
            },
            "custom-wsheaders-http": {
                "headers": {
                    "customRequestHeaders": {"X-Forwarded-Proto": "http"}
                }
            },
            "custom-wsheaders-https": {
                "headers": {
                    "customRequestHeaders": {"X-Forwarded-Proto": "https"}
                }
            },
        }

        # Get host key value from all units
        hosts = self.peers.get_all_unit_values(
            key="host", include_local_unit=True
        )
        novnc_lb_servers = [
            {"url": f"http://{host}:{NOVA_NOVNCPROXY_INGRESS_NAME}"}
            for host in hosts
        ]
        # Add services for heat-api and heat-api-cfn
        service_cfg = {
            f"juju-{model}-{NOVA_NOVNCPROXY_INGRESS_NAME}-service": {
                "loadBalancer": {"servers": novnc_lb_servers},
            },
        }

        config = {
            "http": {
                "routers": router_cfg,
                "middlewares": middleware_cfg,
                "services": service_cfg,
            },
        }
        return config


if __name__ == "__main__":  # pragma: nocover
    ops.main(NovaIronicOperatorCharm)
