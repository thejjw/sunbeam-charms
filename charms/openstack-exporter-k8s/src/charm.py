#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
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
"""Openstack-exporter Operator Charm.

This charm provide Openstack-exporter services as part of an OpenStack deployment
"""

import logging
from typing import (
    TYPE_CHECKING,
    List,
    Optional,
)

import charms.grafana_k8s.v0.grafana_dashboard as grafana_dashboard
import charms.prometheus_k8s.v0.prometheus_scrape as prometheus_scrape
import ops
import ops.charm
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

CONFIGURE_SECRET_PREFIX = "configure-"
CONTAINER = "openstack-exporter"


class OSExporterConfigurationContext(sunbeam_config_contexts.ConfigContext):
    """OSExporter configuration context."""

    if TYPE_CHECKING:
        charm: "OSExporterOperatorCharm"

    @property
    def ready(self) -> bool:
        """Whether the context has all the data is needs."""
        return all(
            (
                self.charm.auth_url is not None,
                self.charm.user_id_ops.ready,
            )
        )

    def context(self) -> dict:
        """OS Exporter configuration context."""
        credentials = self.charm.user_id_ops.get_config_credentials()
        auth_url = self.charm.auth_url
        if credentials is None or auth_url is None:
            return {}
        username, password = credentials
        return {
            "domain_name": self.charm.domain,
            "project_name": self.charm.project,
            "username": username,
            "password": password,
            "auth_url": auth_url,
        }


class OSExporterPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for the container."""

    def get_layer(self) -> dict:
        """Pebble configuration layer for the container."""
        return {
            "summary": "openstack-exporter service",
            "description": ("Pebble config layer for openstack-exporter"),
            "services": {
                self.service_name: {
                    "override": "replace",
                    "summary": "Openstack-Exporter",
                    "command": (
                        "openstack-exporter"
                        " --os-client-config /etc/os-exporter/clouds.yaml"
                        # Using legacy mode as params are not
                        # supported by prometheus_scrape interface
                        " default"
                    ),
                    "user": "_daemon_",
                    "group": "_daemon_",
                    "startup": "disabled",
                },
            },
        }


class MetricsEndpointRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for Metrics Endpoint relation."""

    if TYPE_CHECKING:
        charm: "OSExporterOperatorCharm"
    interface: prometheus_scrape.MetricsEndpointProvider

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for the relation."""
        logger.debug("Setting up Metrics Endpoint event handler")
        interface = prometheus_scrape.MetricsEndpointProvider(
            self.charm, jobs=self.charm._scrape_jobs
        )

        return interface

    @property
    def ready(self) -> bool:
        """Determine with the relation is ready for use."""
        return True


class GrafanaDashboardsRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for Grafana Dashboards relation."""

    interface: grafana_dashboard.GrafanaDashboardProvider

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for the relation."""
        logger.debug("Setting up Grafana Dashboard event handler")
        interface = grafana_dashboard.GrafanaDashboardProvider(
            self.charm,
        )

        return interface

    @property
    def ready(self) -> bool:
        """Determine with the relation is ready for use."""
        return True


class OSExporterOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    mandatory_relations = {
        "identity-ops",
    }
    service_name = "openstack-exporter"

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the operator."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/os-exporter/clouds.yaml",
                "_daemon_",
                "_daemon_",
            ),
        ]

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.append(OSExporterConfigurationContext(self, "os_exporter"))
        return _cadapters

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/os-exporter/clouds.yaml"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "_daemon_"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "_daemon_"

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 9180

    @property
    def os_exporter_user(self) -> str:
        """User for openstack-exporter."""
        return "openstack-exporter"

    @property
    def domain(self):
        """Domain name for openstack-exporter."""
        return "admin_domain"

    @property
    def project(self):
        """Project name for openstack-exporter."""
        return "admin"

    @property
    def _scrape_jobs(self) -> list:
        return [
            # # params not supported by prometheus_scrape interface
            # {
            #     "job_name": "openstack-cloud-metrics",
            #     "metrics_path": "/probe",
            #     # "params": {
            #     #     "cloud": ["default"],
            #     # },
            #     "scrape_timeout": "30s",
            #     "static_configs": [
            #         {
            #             "targets": [
            #                 f"*:{self.default_public_ingress_port}",
            #             ]
            #         }
            #     ],
            # },
            {
                # this will become the internal exporter metrics when
                # probe can be configured with params
                "job_name": "openstack-cloud-metrics",
                "scrape_timeout": "60s",
                "static_configs": [
                    {
                        "targets": [
                            f"*:{self.default_public_ingress_port}",
                        ]
                    }
                ],
            },
        ]

    @property
    def auth_url(self) -> Optional[str]:
        """Auth url for openstack-exporter."""
        label = CONFIGURE_SECRET_PREFIX + "auth-url"
        secret_id = self.leader_get(label)
        if not secret_id:
            return None
        secret = self.model.get_secret(id=secret_id)
        return secret.get_content()["auth-url"]

    def open_ports(self):
        """Register ports in underlying cloud."""
        super().open_ports()
        self.unit.open_port("tcp", self.default_public_ingress_port)

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.user_id_ops = (
            sunbeam_rhandlers.UserIdentityResourceRequiresHandler(
                self,
                "identity-ops",
                self.configure_charm,
                mandatory="identity-ops" in self.mandatory_relations,
                name=self.os_exporter_user,
                domain=self.domain,
                project=self.project,
                project_domain=self.domain,
                role="admin",
                add_suffix=True,
                rotate=ops.SecretRotate.MONTHLY,
                extra_ops=self._get_list_endpoint_ops(),
                extra_ops_process=self._handle_list_endpoint_response,
            )
        )
        handlers.append(self.user_id_ops)
        self.metrics_endpoint = MetricsEndpointRelationHandler(
            self,
            "metrics-endpoint",
            self.configure_charm,
            mandatory="metrics-endpoint" in self.mandatory_relations,
        )
        handlers.append(self.metrics_endpoint)
        self.grafana_dashboard = GrafanaDashboardsRelationHandler(
            self,
            "grafana-dashboard",
            self.configure_charm,
            mandatory="grafana-dashboard" in self.mandatory_relations,
        )
        handlers.append(self.grafana_dashboard)
        return handlers

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for operator."""
        return [
            OSExporterPebbleHandler(
                self,
                CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]

    def _get_list_endpoint_ops(self) -> list:
        """Generate ops request for list endpoint."""
        return [
            {
                "name": "list_endpoint",
                "params": {"name": "keystone", "interface": "admin"},
            }
        ]

    def _retrieve_or_set_config_secret(
        self,
        key: str,
        value: str,
        rotate: ops.SecretRotate = ops.SecretRotate.NEVER,
    ) -> str:
        """Retrieve or create a secret."""
        label = CONFIGURE_SECRET_PREFIX + key
        credentials_id = self.leader_get(label)
        if credentials_id:
            secret = self.model.get_secret(id=credentials_id)
            content = secret.get_content()
            if content[key] != value:
                content[key] = value
                secret.set_content(content)
            return credentials_id

        credentials_secret = self.model.app.add_secret(
            {key: value},
            label=label,
            rotate=rotate,
        )
        self.leader_set(
            {
                label: credentials_secret.id,
            }
        )
        return credentials_secret.id

    def _handle_list_endpoint_response(
        self, event: ops.EventBase, response: dict
    ) -> None:
        """Handle response from identity-ops."""
        logger.info("%r", response)
        if {
            op.get("return-code")
            for op in response.get(
                "ops",
                [],
            )
        } == {0}:
            logger.debug(
                "Initial openstack exporter user setup commands completed,"
                " running configure charm"
            )
            for op in response.get("ops", []):
                if op.get("name") != "list_endpoint":
                    continue
                for endpoint in op.get("value", []):
                    url = endpoint.get("url")
                    logger.info("url %r", url)
                    if url is not None:
                        self._retrieve_or_set_config_secret("auth-url", url)
                        self.configure_charm(event)
                        break


if __name__ == "__main__":
    main(OSExporterOperatorCharm)
