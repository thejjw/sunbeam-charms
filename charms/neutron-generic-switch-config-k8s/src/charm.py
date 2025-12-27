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

"""OpenStack Neutron Generic Switch Config Charm.

This is a config charm for the OpenStack neutron-k8s charm, providing it with
necessary details for its ml2_conf_genericswitch.ini config file.
"""

import configparser
import logging
import os
from typing import (
    List,
)

import charms.neutron_k8s.v0.switch_config as switch_config
import ops
import ops_sunbeam.guard as sunbeam_guard

logger = logging.getLogger(__name__)

SWITCH_CONFIG_RELATION_NAME = "switch-config"

_GENERIC_CONFIG_OPTIONS = {
    "device_type",
    "ip",
    "port",
    "username",
    "password",
    "use_keys",
    "key_file",
    "secret",
    # NGS internal opts.
    # https://github.com/openstack/networking-generic-switch/blob/unmaintained/2024.1/networking_generic_switch/devices/__init__.py#L28
    "ngs_mac_address",
    "ngs_trunk_ports",
    "ngs_port_default_vlan",
    "ngs_physical_networks",
    "ngs_ssh_disabled_algorithms",
    "ngs_ssh_connect_timeout",
    "ngs_ssh_connect_interval",
    "ngs_max_connections",
    "ngs_switchport_mode",
    "ngs_disable_inactive_ports",
    "ngs_network_name_format",
    "ngs_manage_vlans",
    "ngs_save_configuration",
    "ngs_batch_requests",
    "ngs_fake_sleep_min_s",
    "ngs_fake_sleep_max_s",
    "ngs_fake_failure_prob",
}

_NEUTRON_SSHKEYS_PATH = "/etc/neutron/sshkeys"


class NeutronGenericSwitchConfigCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        # Lifecycle events
        self.framework.observe(
            self.on.config_changed,
            self._on_config_changed,
        )

        self.switch_config_handler = switch_config.SwitchConfigProvides(
            self, SWITCH_CONFIG_RELATION_NAME
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

                config = configparser.ConfigParser()
                config.read_string(content["conf"])
            except configparser.Error as ex:
                logger.error(
                    "Could not decode config from secret %s. Error: %s",
                    secret.id,
                    ex,
                )
                raise sunbeam_guard.BlockedExceptionError(
                    f"Invalid content in secret {secret.id}. Check logs."
                )

            for name in config.sections():
                if name in all_configs:
                    # Duplicate config section, misconfiguration.
                    logger.error(
                        "Duplicate config section (%s) found in secret '%s'.",
                        name,
                        secret.id,
                    )
                    raise sunbeam_guard.BlockedExceptionError(
                        "Duplicate config section found in configured conf-secrets. Check logs."
                    )

                section = config[name]
                self._validate_section(secret.id, name, section)

                key_file = section.get("key_file")
                if not key_file:
                    continue

                path, filename = os.path.split(key_file)
                if path != _NEUTRON_SSHKEYS_PATH:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Expected key_file config option from secret '{secret.id}' "
                        f"to have the base path {_NEUTRON_SSHKEYS_PATH}."
                    )

                if filename not in content:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Missing '{filename}' additional file from secret '{secret.id}'"
                    )

            all_configs.update(config)

    def _validate_section(self, secret_id, section_name, section):
        # sections need to start with 'genericswitch:':
        # https://github.com/openstack/networking-generic-switch/blob/55d0729c01df8eaab4ebeda466cd7fa3c2c758c4/networking_generic_switch/config.py#L72
        if not section_name.startswith("genericswitch:"):
            msg = (
                f"The section '{section_name}' in secret '{secret_id}' does not "
                "start with 'genericswitch:', as needed by the genericswitch "
                "mechanism driver."
            )
            logger.error(msg)
            raise sunbeam_guard.BlockedExceptionError(msg)
        if not section.get("device_type"):
            logger.error(
                "Field 'device_type' missing from section '%s' in secret '%s'",
                section_name,
                secret_id,
            )
            raise sunbeam_guard.BlockedExceptionError(
                f"Missing mandatory field from secret {secret_id}. Check logs."
            )

        section_keys = set(section.keys())
        different_keys = section_keys - _GENERIC_CONFIG_OPTIONS
        if different_keys:
            logger.error(
                "Unknown fields found in secret '%s', in section '%s': %s",
                secret_id,
                section_name,
                different_keys,
            )
            raise sunbeam_guard.BlockedExceptionError(
                f"Unknown fields found in secret {secret_id}. Check logs."
            )


if __name__ == "__main__":  # pragma: nocover
    ops.main(NeutronGenericSwitchConfigCharm)
