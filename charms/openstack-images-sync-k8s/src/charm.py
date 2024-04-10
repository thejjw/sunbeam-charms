#!/usr/bin/env python3

#
# Copyright 2024 Canonical Ltd.
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

"""Openstack Images Sync Operator.

This charm deploys the openstack images sync service on Kubernetes.
"""

import logging
import os
from typing import (
    TYPE_CHECKING,
)

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
from charms.keystone_k8s.v1.identity_service import (
    IdentityServiceRequires,
)

logger = logging.getLogger(__name__)


def _frequency_to_seconds(frequency: str) -> int:
    """Convert given frequency word to seconds.

    test-do-not-use is for testing purposes only.
    """
    match frequency:
        case "hourly":
            return 3600
        case "daily":
            return 86400
        case "weekly":
            return 604800
        case "test-do-not-use":
            return 30
        case _:
            raise ValueError(f"Unknown frequency {frequency!r}")


class SyncCharmConfigContext(sunbeam_config_contexts.CharmConfigContext):
    """Configure context for templates."""

    def context(self) -> dict:
        """Return context for template rendering."""
        return {
            **self.charm.config,
            "architecture": "|".join(
                self.charm.config["architecture"].split()
            ),
            "release": "|".join(self.charm.config["release"].split()),
            "region": ", ".join(self.charm.config["region"].split()),
            "frequency": _frequency_to_seconds(self.charm.config["frequency"]),
        }


class HttpSyncConfigContext(sunbeam_config_contexts.ConfigContext):
    """Configuration context for the http sync service."""

    if TYPE_CHECKING:
        charm: "OpenstackImagesSyncK8SCharm"

    def context(self) -> dict:
        """Httpd configuration options."""
        return {
            "name": self.charm.service_name,
            "public_port": self.charm.default_public_ingress_port,
            "error_log": "/dev/stdout",
            "custom_log": "/dev/stdout",
        }


class OpenstackImagesSyncPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Handler for openstack images sync container."""

    if TYPE_CHECKING:
        charm: "OpenstackImagesSyncK8SCharm"

    @property
    def directories(self) -> list[sunbeam_chandlers.ContainerDir]:
        """List of directories to create in container."""
        return [
            sunbeam_chandlers.ContainerDir(
                "/var/www/html/simplestreams",
                self.charm.service_user,
                self.charm.service_group,
            ),
        ]

    def get_layer(self) -> dict:
        """Openstack Images Sync service pebble layer.

        :returns: pebble layer configuration for openstack images sync service
        """
        return {
            "summary": f"{self.service_name} layer",
            "description": f"pebble config layer for {self.service_name}",
            "services": {
                "images-sync": {
                    "override": "replace",
                    "summary": self.service_name,
                    "command": (
                        "/usr/bin/openstack-images-sync sync"
                        " --config /etc/openstack-images-sync/config.yaml"
                    ),
                    "user": "_daemon_",
                    "group": "_daemon_",
                    "environment": {
                        **self.charm.keystone_auth(),
                        **self.charm.proxy_env(),
                    },
                },
                "http-mirror": {
                    "override": "replace",
                    "summary": "apache",
                    "command": "/usr/sbin/apache2ctl -DFOREGROUND",
                },
            },
        }

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Initialise service ready for use.

        Write configuration files to the container and record
        that service is ready for us. Enable apache modules.
        """
        self.execute(["a2dissite", "000-default"], exception_on_error=True)
        return super().init_service(context)


class OpenstackImagesSyncK8SCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the application."""

    service_name = "openstack-images-sync"
    mandatory_relations = {
        "identity-service",
        "ingress-public",
    }

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/openstack-images-sync/config.yaml"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "_daemon_"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "_daemon_"

    @property
    def default_public_ingress_port(self) -> int:
        """Default ingress port."""
        return 80

    @property
    def service_endpoints(self):
        """Describe the openstack images sync service endpoint."""
        slash_region = "/" + self.config["region"].split()[0]
        return [
            {
                "service_name": "image-stream",
                "type": "product-streams",
                "description": "Image stream service",
                "internal_url": self.internal_url + slash_region,
                "public_url": self.public_url + slash_region,
                "admin_url": self.admin_url + slash_region,
            }
        ]

    @property
    def config_contexts(self) -> list[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        return [
            SyncCharmConfigContext(self, "options"),
            # don't use wsgi-context here
            HttpSyncConfigContext(self, "httpd_config"),
        ]

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            OpenstackImagesSyncPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                self.service_conf,
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/apache2/sites-enabled/http-sync.conf",
                "root",
                "root",
                0o640,
            ),
        ]
        return _cconfigs

    def keystone_auth(self) -> dict[str, str]:
        """Keystone authentication."""
        if self.id_svc.ready:
            interface: IdentityServiceRequires = self.id_svc.interface  # type: ignore
            return {
                # Using public auth url because openstack-images-sync will output an index.json
                # referencing this auth url. Later on, client will want to match the clouds and
                # will use a combo of auth_url + region.
                "OS_AUTH_URL": interface.public_auth_url,
                "OS_IDENTITY_API_VERSION": "3",
                "OS_USERNAME": interface.service_user_name,
                "OS_PASSWORD": interface.service_password,
                "OS_PROJECT_NAME": interface.service_project_name,
                "OS_USER_DOMAIN_NAME": interface.service_domain_name,
                "OS_PROJECT_DOMAIN_NAME": interface.service_domain_name,
            }
        return {}

    def proxy_env(self) -> dict[str, str]:
        """Get proxy settings from environment."""
        juju_proxy_vars = [
            "JUJU_CHARM_HTTP_PROXY",
            "JUJU_CHARM_HTTPS_PROXY",
            "JUJU_CHARM_NO_PROXY",
        ]
        return {
            proxy_var.removeprefix("JUJU_CHARM_"): value
            for proxy_var in juju_proxy_vars
            if (value := os.environ.get(proxy_var))
        }


if __name__ == "__main__":  # pragma: nocover
    ops.main(OpenstackImagesSyncK8SCharm)  # type: ignore
