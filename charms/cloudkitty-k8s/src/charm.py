#!/usr/bin/env python3

#
# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://apache.org
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Cloudkitty Operator Charm.

This charm provide Cloudkitty services as part of an OpenStack deployment
"""

import logging
from typing import (
    Dict,
    List,
    Mapping,
)

import ops
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from charms.loki_k8s.v1.loki_push_api import (
    LokiPushApiConsumer,
)

logger = logging.getLogger(__name__)

CLOUDKITTY_API_PORT = 8889
CLOUDKITTY_CONTAINER = "cloudkitty"


class LokiLoggingRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Custom Sunbeam Relation Handler for the Loki logging interface."""

    def setup_event_handler(self) -> ops.framework.Object:
        """Initialize the library interface consumer and register events."""
        logger.debug("Setting up Loki logging interface consumer")
        self.loki_consumer = LokiPushApiConsumer(
            self.charm, relation_name=self.relation_name
        )

        rname = self.relation_name.replace("-", "_")
        logging_event = getattr(self.charm.on, f"{rname}_relation_changed")
        self.framework.observe(logging_event, self._on_logging_changed)

        return self.loki_consumer

    def _on_logging_changed(self, event: ops.framework.EventBase) -> None:
        """Callback to trigger charm reconfiguration when relation details shift."""
        logger.info(f"Loki logging integration event received: {event}")
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether the handler has successfully captured relation endpoints."""
        return True

    def _parse_endpoint_string(self, raw_endpoint: str) -> str:
        """Safely extract the URL out of raw text strings or structured JSON."""
        if not raw_endpoint:
            return ""
        if '"url"' in raw_endpoint:
            import json

            try:
                return json.loads(raw_endpoint).get("url", "")
            except Exception:
                return ""
        return raw_endpoint

    def context(self) -> dict:
        """Expose relation parameters to the global template context environment."""
        endpoints = (
            self.interface.loki_endpoints
            if hasattr(self.interface, "loki_endpoints")
            else []
        )
        loki_url = ""

        # Extract the live URL cleanly using the native library endpoints wrapper
        if endpoints and isinstance(endpoints, list):
            for endpoint in endpoints:
                val = (
                    endpoint.get("url", "")
                    if isinstance(endpoint, dict)
                    else endpoint
                )
                if val:
                    loki_url = val
                    break

        logger.info(f"RELATION HANDLER EXTRACTED ENDPOINT: '{loki_url}'")
        return {"logging_endpoints": loki_url}


@sunbeam_tracing.trace_type
class CloudkittyWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for Cloudkitty WSGI services."""

    @property
    def wsgi_conf(self) -> str:
        """Location of WSGI config file matching template naming convention."""
        return f"/etc/apache2/sites-available/wsgi-{self.service_name}.conf"

    def start_service(self):
        """Start services in container."""
        pass

    def init_service(self, context) -> None:
        """Enable and start WSGI service matching our updated filename."""
        self.write_config(context)
        try:
            self.execute(
                ["a2dissite", f"wsgi-{self.service_name}"],
                exception_on_error=True,
            )
            self.execute(
                ["a2ensite", f"wsgi-{self.service_name}"],
                exception_on_error=True,
            )
        except ops.pebble.ExecError:
            logger.exception(
                f"Failed to enable wsgi-{self.service_name} site in apache"
            )

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer configuration."""
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "exec": {"command": "service apache2 status"},
                },
            }
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Generate default configuration files for container."""
        return [
            sunbeam_core.ContainerConfigFile(self.wsgi_conf, "root", "root"),
            sunbeam_core.ContainerConfigFile(
                "/etc/cloudkitty/cloudkitty.conf", "root", "cloudkitty", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/cloudkitty/api_audit_map.conf",
                "root",
                "cloudkitty",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "cloudkitty",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_type
class CloudkittyProcessorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Cloudkitty Processor services."""

    def get_layer(self) -> dict:
        """Cloudkitty Processor service."""
        return {
            "summary": "cloudkitty layer",
            "description": "pebble configuration for cloudkitty services",
            "services": {
                "cloudkitty-processor": {
                    "override": "replace",
                    "summary": "Cloudkitty Processor",
                    "command": "cloudkitty-processor --use-syslog",
                    "user": "cloudkitty",
                    "group": "cloudkitty",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Generate default configuration files for container."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/cloudkitty/cloudkitty.conf", "root", "cloudkitty", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "cloudkitty",
                0o640,
            ),
        ]


@sunbeam_tracing.trace_sunbeam_charm
class CloudkittyOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Cloudkitty the service."""

    _authed = False
    service_name = "cloudkitty"
    wsgi_admin_script = "/usr/bin/cloudkitty-api"
    wsgi_public_script = "/usr/bin/cloudkitty-api"

    db_sync_cmds = [
        ["cloudkitty-dbsync", "upgrade"],
        ["cloudkitty-storage-init"],
    ]

    def __init__(self, *args):
        """Run constructor setup."""
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, event: ops.EventBase):
        """Handle standard configuration change events."""
        self.configure_charm(event)

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("gnocchi-db", handlers):
            self.gnocchi_svc = sunbeam_rhandlers.GnocchiServiceRequiresHandler(
                self,
                "gnocchi-db",
                self.configure_charm,
                "gnocchi-db" in self.mandatory_relations,
            )
            handlers.append(self.gnocchi_svc)

        # Inject our custom logging handler cleanly under the 'logging' key
        if self.can_add_handler("logging", handlers):
            self.logging_handler = LokiLoggingRelationHandler(
                self, "logging", self.configure_charm
            )
            handlers.append(self.logging_handler)

        return super().get_relation_handlers(handlers)

    @property
    def service_endpoints(self) -> List[Dict]:
        """Service endpoints for the Cloudkitty API services."""
        return [
            {
                "service_name": "cloudkitty",
                "type": "rating",
                "description": "OpenStack Rating Service",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            },
        ]

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for cloudkitty services."""
        return {"database": "cloudkitty"}

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the charm."""
        pebble_handlers = [
            CloudkittyWSGIPebbleHandler(
                self,
                CLOUDKITTY_CONTAINER,
                "cloudkitty",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}-api",
            ),
            CloudkittyProcessorPebbleHandler(
                self,
                CLOUDKITTY_CONTAINER,
                "cloudkitty-processor",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def default_public_ingress_port(self):
        """Public ingress port for service."""
        return 8889

    @property
    def ingress_healthcheck_path(self):
        """Healthcheck path for ingress relation."""
        return "/healthcheck"

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/cloudkitty/cloudkitty.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "cloudkitty"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "cloudkitty"

    @property
    def db_sync_container_name(self) -> str:
        """Name of Containerto run db sync from."""
        return CLOUDKITTY_CONTAINER

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logging.debug("Service checks enabled for cloudkitty worker")
            return super().service_ready
        else:
            logging.debug("Service checks disabled for cloudkitty worker")
            return self.pebble_ready


if __name__ == "__main__":  # pragma: nocover
    ops.main(CloudkittyOperatorCharm)
