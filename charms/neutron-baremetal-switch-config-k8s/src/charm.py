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

"""OpenStack Neutron Baremetal Switch Config Charm.

This is a config charm for the OpenStack neutron-k8s charm, providing it with
necessary details for its ml2_conf_networking_baremetal.ini config file.
"""

import base64
import binascii
import logging
import tomllib
from typing import (
    Dict,
    List,
)

import charms.neutron_k8s.v0.switch_config as switch_config
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)

SWITCH_CONFIG_RELATION_NAME = "switch-config"

_NETCONF_CONFIG_OPTIONS = {
    "driver",
    "switch_id",
    "switch_info",
    "physical_networks",
    "manage_vlans",
    "network_instance",
    "port_id_re_sub",
    "disabled_properties",
    "manage_lacp_aggregates",
    "link_aggregate_prefix",
    "link_aggregate_range",
    "host",
    "username",
    "port",
    "password",
    "key_filename",
    "hostkey_verify",
    "device_params",
    "allow_agent",
    "look_for_keys",
}


@sunbeam_tracing.trace_sunbeam_charm
class NeutronBaremetalSwitchConfigCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        # Lifecycle events
        self.framework.observe(
            self.on.config_changed,
            self._on_config_changed,
        )

        self.switch_config_handler = (
            sunbeam_tracing.trace_type(switch_config.SwitchConfigProvides)(
                self, SWITCH_CONFIG_RELATION_NAME
            )
        )

        self.framework.observe(
            self.switch_config_handler.on.switch_config_connected,
            self._on_switch_config,
        )

    def _on_config_changed(self, event: ops.framework.EventBase) -> None:
        """Handle the config change event."""
        if not self.unit.is_leader():
            return

        try:
            self._update_switch_config_relation()
        except sunbeam_guard.BaseStatusExceptionError as ex:
            self.unit.status = ex.to_status()
            return

        self.unit.status = ops.ActiveStatus("Provider is ready")

    def _on_switch_config(self, event: ops.framework.EventBase) -> None:
        """Handle the switch-config relation data."""
        if not self.unit.is_leader():
            return

        try:
            self._update_switch_config_relation()
        except sunbeam_guard.BaseStatusExceptionError as ex:
            self.unit.status = ex.to_status()

    def _update_switch_config_relation(self):
        secrets = self._get_secrets()
        self._validate_configs(secrets)

        self.switch_config_handler.grant_secrets(secrets)
        secret_ids = self.config.get("conf-secrets")
        self.switch_config_handler.update_switch_configs(secret_ids)

    def _get_secrets(self) -> List[ops.model.Secret]:
        secret_ids = self.config.get("conf-secrets")
        if not secret_ids:
            raise sunbeam_guard.BlockedExceptionError(
                "Config option conf-secrets needs to be set."
            )

        all_secrets = []
        for secret_id in secret_ids.split(","):
            try:
                secret = self.model.get_secret(id=secret_id)
                all_secrets.append(secret)
            except ops.model.SecretNotFoundError:
                raise sunbeam_guard.BlockedExceptionError(
                    f"Secret {secret_id} not found."
                )

        return all_secrets

    def _validate_configs(self, secrets: List[ops.model.Secret]):
        all_configs = {}
        for secret in secrets:
            try:
                content = secret.get_content()
                if "conf" not in content:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Expected {secret.id} to contain 'conf' key."
                    )

                config = tomllib.loads(content["conf"])
            except tomllib.TOMLDecodeError as ex:
                logger.error("Could not decode TOML from secret %s. Error: %s", secret.id, ex)
                raise sunbeam_guard.BlockedExceptionError(
                    f"Invalid content in secret {secret.id}. Check logs."
                )

            for name in config.keys():
                if name in all_configs:
                    # Duplicate config section, misconfiguration.
                    logger.error("Duplicate config section (%s) found in secret '%s'.", name, secret.id)
                    raise sunbeam_guard.BlockedExceptionError(
                        "Duplicate config section found in configured conf-secrets. Check logs."
                    )

                section = config[name]
                self._validate_section(secret.id, name, section)

                key_filename = section.get("key_filename")
                if not key_filename:
                    continue

                key_filename = key_filename.split("/")[-1].replace("_", "-")
                if key_filename not in content:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Missing '{key_filename}' additional file from secret '{secret.id}'"
                    )

            all_configs.update(config)

    def _validate_section(self, secret_id, section_name, section):
        section_keys = set(section.keys())
        different_keys = section_keys - _NETCONF_CONFIG_OPTIONS
        if different_keys:
            logger.error("Unknown fields found in secret '%s', in section '%s': %s", secret_id, section_name, different_keys)
            raise sunbeam_guard.BlockedExceptionError(
                f"Unknown fields found in secret {secret_id}. Check logs."
            )


if __name__ == "__main__":  # pragma: nocover
    ops.main(NeutronBaremetalSwitchConfigCharm)
