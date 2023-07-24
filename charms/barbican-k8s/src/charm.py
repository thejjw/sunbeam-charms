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

"""Barbican Operator Charm.

This charm provide Barbican services as part of an OpenStack deployment
"""
import logging
from typing import (
    List,
)

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
from ops import (
    framework,
    model,
    pebble,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

BARBICAN_API_CONTAINER = "barbican-api"
BARBICAN_WORKER_CONTAINER = "barbican-worker"


class WSGIBarbicanAdminConfigContext(sunbeam_ctxts.ConfigContext):
    """Configuration context for WSGI configuration."""

    def context(self) -> dict:
        """WSGI configuration options."""
        return {
            "name": self.charm.service_name,
            "public_port": 9312,
            "user": self.charm.service_user,
            "group": self.charm.service_group,
            "wsgi_admin_script": "/usr/bin/barbican-wsgi-api",
            "wsgi_public_script": "/usr/bin/barbican-wsgi-api",
            "error_log": "/dev/stdout",
            "custom_log": "/dev/stdout",
        }


class BarbicanWorkerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Barbican worker."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Barbican worker service layer.

        :returns: pebble layer configuration for worker service
        :rtype: dict
        """
        return {
            "summary": "barbican worker layer",
            "description": "pebble configuration for barbican worker",
            "services": {
                "barbican-worker": {
                    "override": "replace",
                    "summary": "Barbican Worker",
                    "command": "barbican-worker",
                    "user": "barbican",
                    "group": "barbican",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/barbican/barbican.conf", "barbican", "barbican"
            )
        ]

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logging.debug("Service checks enabled for barbican worker")
            return super().service_ready
        else:
            logging.debug("Service checks disabled for barbican worker")
            return self.pebble_ready


class BarbicanOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = framework.StoredState()
    service_name = "barbican-api"
    wsgi_admin_script = "/usr/bin/barbican-wsgi-api"
    wsgi_public_script = "/usr/bin/barbican-wsgi-api"
    mandatory_relations = {
        "database",
        "amqp",
        "identity-service",
        "ingress-public",
    }

    db_sync_cmds = [
        ["sudo", "-u", "barbican", "barbican-manage", "db", "upgrade"]
    ]

    def configure_unit(self, event: framework.EventBase) -> None:
        """Run configuration on this unit."""
        self.disable_barbican_config()
        super().configure_unit(event)

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend(
            [
                WSGIBarbicanAdminConfigContext(
                    self,
                    "wsgi_barbican_admin",
                )
            ]
        )
        return _cadapters

    @sunbeam_job_ctrl.run_once_per_unit("a2disconf")
    def disable_barbican_config(self):
        """Disable default barbican config."""
        container = self.unit.get_container(BARBICAN_API_CONTAINER)
        try:
            process = container.exec(
                ["a2disconf", "barbican-api"], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2disconf warn: %s", line.strip())
            logging.debug(f"Output from a2disconf: \n{out}")
        except pebble.ExecError:
            logger.exception("Failed to disable barbican-api conf in apache")
            self.status = model.ErrorStatus(
                "Failed to disable barbican-api conf in apache"
            )

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = super().get_pebble_handlers()
        pebble_handlers.extend(
            [
                BarbicanWorkerPebbleHandler(
                    self,
                    BARBICAN_WORKER_CONTAINER,
                    "barbican-worker",
                    [],
                    self.template_dir,
                    self.configure_charm,
                ),
            ]
        )
        return pebble_handlers

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/barbican/barbican.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "barbican"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "barbican"

    @property
    def service_endpoints(self):
        """Service endpoints configuration."""
        return [
            {
                "service_name": "barbican",
                "type": "key-manager",
                "description": "OpenStack Barbican API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default port."""
        return 9311

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        # / returns a 300 return code, which is not understood by Pebble as OK
        return super().healthcheck_http_url + "?build"


if __name__ == "__main__":
    main(BarbicanOperatorCharm)
