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
import secrets
from typing import (
    List,
    Optional,
)

import ops
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.vault_k8s.v0 import (
    vault_kv,
)
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
VAULT_KV_RELATION = "vault-kv"
NONCE_SECRET_LABEL = "nonce"


class NoRelationError(Exception):
    """No relation found."""

    pass


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


class VaultKvRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for vault-kv relation."""

    charm: "BarbicanVaultOperatorCharm"
    interface: vault_kv.VaultKvRequires

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        callback_f,
        mount_suffix: str,
        mandatory: bool = False,
    ):
        self.mount_suffix = mount_suffix
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for a vault-kv relation."""
        logger.debug("Setting up vault-kv event handler")
        interface = vault_kv.VaultKvRequires(
            self.charm,
            self.relation_name,
            self.mount_suffix,
        )

        self.framework.observe(interface.on.connected, self._on_connected)
        self.framework.observe(interface.on.ready, self._on_ready)
        self.framework.observe(interface.on.gone_away, self._on_gone_away)
        try:
            self.request_credentials(interface, self._relation)
        except NoRelationError:
            pass
        return interface

    @property
    def _relation(self) -> ops.Relation:
        relation = self.model.get_relation(VAULT_KV_RELATION)
        if relation is None:
            raise NoRelationError("Vault-kv relation not found")
        return relation

    def _on_connected(self, event: vault_kv.VaultKvConnectedEvent):
        """Handle on connected event."""
        relation = self.model.get_relation(
            event.relation_name, event.relation_id
        )
        if relation is None:
            raise RuntimeError(
                "Vault-kv relation not found during a connected event"
            )
        self.request_credentials(self.interface, relation)

    def _on_ready(self, event: vault_kv.VaultKvReadyEvent):
        """Handle client ready relation."""
        self.callback_f(event)

    def _on_gone_away(self, event: vault_kv.VaultKvGoneAwayEvent):
        """Handle client gone away relation."""
        self.callback_f(event)

    def request_credentials(
        self, interface: vault_kv.VaultKvRequires, relation: ops.Relation
    ):
        """Request credentials from vault-kv relation."""
        nonce = self.charm.get_nonce()
        if nonce is None:
            return
        binding = self.model.get_binding(relation)
        if binding is None:
            logger.debug("No binding found for vault-kv relation")
            return
        egress_subnet = str(binding.network.interfaces[0].subnet)
        interface.request_credentials(relation, egress_subnet, nonce)

    @property
    def ready(self) -> bool:
        """Whether the handler is ready for use."""
        relation = self.model.get_relation(VAULT_KV_RELATION)
        if relation is None:
            return False
        return all(
            (
                self.interface.get_unit_credentials(relation),
                self.interface.get_vault_url(relation),
                self.interface.get_mount(relation),
            )
        )

    def context(self) -> dict:
        """Context containing relation data."""
        vault_kv_relation = self._relation
        unit_credentials = self.interface.get_unit_credentials(
            vault_kv_relation
        )
        if not unit_credentials:
            return {}
        secret = self.model.get_secret(id=unit_credentials)
        secret_content = secret.get_content()
        return {
            "kv_mountpoint": self.interface.get_mount(vault_kv_relation),
            "vault_url": self.interface.get_vault_url(vault_kv_relation),
            "approle_role_id": secret_content["role-id"],
            "approle_secret_id": secret_content["role-secret-id"],
            "ca_crt_file": self.charm.ca_crt_file,
            "ca_certificate": self.interface.get_ca_certificate(
                vault_kv_relation
            ),
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


class BarbicanVaultOperatorCharm(BarbicanOperatorCharm):
    """Vault specialized Barbican Operator Charm."""

    mandatory_relations = BarbicanOperatorCharm.mandatory_relations.union(
        {VAULT_KV_RELATION}
    )

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)

    def _on_install(self, event: ops.framework.EventBase) -> None:
        """Handle install event."""
        self.unit.add_secret(
            {"nonce": secrets.token_hex(16)},
            label=NONCE_SECRET_LABEL,
            description="nonce for vault-kv relation",
        )

    def get_relation_handlers(
        self,
        handlers: Optional[List[sunbeam_rhandlers.RelationHandler]] = None,
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers(handlers)
        if self.can_add_handler(VAULT_KV_RELATION, handlers):
            self.vault_kv = VaultKvRequiresHandler(
                self,
                VAULT_KV_RELATION,
                self.configure_charm,
                self.mount_suffix,
                VAULT_KV_RELATION in self.mandatory_relations,
            )
            handlers.append(self.vault_kv)
        return handlers

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.append(
            sunbeam_core.ContainerConfigFile(
                self.ca_crt_file, "barbican", "barbican"
            )
        )
        return _cconfigs

    @property
    def mount_suffix(self):
        """Secret backend for vault."""
        return "secrets"

    @property
    def ca_crt_file(self):
        """Vault CA certificate file location."""
        return "/etc/barbican/vault_ca.crt"

    def get_nonce(self) -> Optional[str]:
        """Return nonce stored in secret."""
        try:
            secret = self.model.get_secret(label=NONCE_SECRET_LABEL)
            return secret.get_content()["nonce"]
        except ops.SecretNotFoundError:
            return None


if __name__ == "__main__":
    main(BarbicanVaultOperatorCharm)
