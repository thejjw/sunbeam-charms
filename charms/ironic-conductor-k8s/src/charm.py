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

"""Ironic Conductor Operator Charm.

This charm provides Ironic Conductor service as part of an OpenStack
deployment.
"""

import hashlib
import logging
import uuid
from typing import (
    List,
    Mapping,
)

import api_utils
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing
from ops.charm import (
    RelationChangedEvent,
)

logger = logging.getLogger(__name__)

IRONIC_CONDUCTOR_CONTAINER = "ironic-conductor"
VALID_NETWORK_INTERFACES = ["neutron", "flat", "noop"]
VALID_DEPLOY_INTERFACES = ["direct"]

# The IPMI HW type requires only ipmitool to function. This HW type
# remains pretty much unchanged across OpenStack releases and *should*
# work
_NOOP_INTERFACES = {
    "enabled_bios_interfaces": "no-bios",
    "enabled_console_interfaces": "no-console",
    "enabled_inspect_interfaces": "no-inspect",
    "enabled_management_interfaces": "noop",
    "enabled_raid_interfaces": "no-raid",
    "enabled_vendor_interfaces": "no-vendor",
}

_FAKE_HARDWARE_TYPE = {
    "config_options": {
        "enabled_bios_interfaces": ["fake"],
        "enabled_boot_interfaces": ["fake"],
        "enabled_console_interfaces": ["fake"],
        "enabled_deploy_interfaces": ["fake"],
        "enabled_hardware_types": ["fake-hardware"],
        "enabled_inspect_interfaces": ["fake"],
        "enabled_management_interfaces": ["fake"],
        "enabled_power_interfaces": ["fake"],
        "enabled_raid_interfaces": ["fake"],
        "enabled_vendor_interfaces": ["fake"],
    },
}

_IPMI_HARDWARE_TYPE = {
    "config_options": {
        "enabled_bios_interfaces": [],
        "enabled_boot_interfaces": ["pxe"],
        "enabled_console_interfaces": [
            "ipmitool-socat",
            "ipmitool-shellinabox",
        ],
        "enabled_deploy_interfaces": ["direct"],
        "enabled_hardware_types": ["ipmi", "intel-ipmi"],
        "enabled_inspect_interfaces": [],
        "enabled_management_interfaces": ["ipmitool", "intel-ipmitool"],
        "enabled_power_interfaces": ["ipmitool"],
        "enabled_raid_interfaces": [],
        "enabled_vendor_interfaces": ["ipmitool"],
    },
}

_REDFISH_HARDWARE_TYPE = {
    "config_options": {
        "enabled_boot_interfaces": ["pxe", "redfish-virtual-media"],
        "enabled_bios_interfaces": [],
        "enabled_console_interfaces": [],
        "enabled_deploy_interfaces": ["direct"],
        "enabled_hardware_types": ["redfish"],
        "enabled_inspect_interfaces": ["redfish"],
        "enabled_management_interfaces": ["redfish"],
        "enabled_power_interfaces": ["redfish"],
        "enabled_raid_interfaces": [],
        "enabled_vendor_interfaces": [],
    },
}

_IDRAC_HARDWARE_TYPE = {
    "config_options": {
        "enabled_bios_interfaces": ["idrac-wsman"],
        "enabled_boot_interfaces": ["pxe"],
        "enabled_console_interfaces": [],
        "enabled_deploy_interfaces": ["direct"],
        "enabled_hardware_types": ["idrac"],
        "enabled_inspect_interfaces": ["idrac-redfish"],
        "enabled_management_interfaces": ["idrac-redfish"],
        "enabled_power_interfaces": ["idrac-redfish"],
        "enabled_raid_interfaces": ["idrac-wsman"],
        "enabled_vendor_interfaces": ["idrac-wsman"],
    },
}

_HW_TYPES_MAP = {
    "fake": _FAKE_HARDWARE_TYPE,
    "ipmi": _IPMI_HARDWARE_TYPE,
    "idrac": _IDRAC_HARDWARE_TYPE,
    "redfish": _REDFISH_HARDWARE_TYPE,
}


@sunbeam_tracing.trace_type
class IronicConductorConfigurationContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set ironic parameters."""

    def context(self) -> dict:
        """Generate configuration information for ironic_config."""
        ctxt = {
            "hardware_type_cfg": self._get_hardware_types_config(),
            "temp_url_secret": self.charm.leader_get("temp_url_secret"),
        }

        return ctxt

    def _get_hardware_types_config(self):
        configs = {}
        for hw_type in self.charm.enabled_hw_types:
            details = _HW_TYPES_MAP.get(hw_type, None)
            if details is None:
                # Not a valid hardware type. No need to raise here,
                # we will let the operator know when we validate the
                # config in custom_assess_status_check()
                continue
            driver_cfg = details["config_options"]
            for cfg_opt in driver_cfg.items():
                opt_list = configs.get(cfg_opt[0], [])
                opt_list.extend(cfg_opt[1])
                opt_list = list(set(opt_list))
                opt_list.sort()
                configs[cfg_opt[0]] = opt_list

        if self.charm.config.get("use-ipxe", None):
            configs["enabled_boot_interfaces"].append("ipxe")

        # append the noop interfaces at the end
        for noop in _NOOP_INTERFACES:
            if configs.get(noop, None) is not None:
                configs[noop].append(_NOOP_INTERFACES[noop])

        for opt in configs:
            if len(configs[opt]) > 0:
                configs[opt] = ", ".join(configs[opt])
            else:
                configs[opt] = ""

        return configs


@sunbeam_tracing.trace_type
class IronicConductorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Ironic Conductor."""

    @property
    def directories(self) -> list[sunbeam_chandlers.ContainerDir]:
        """List of directories to create in container."""
        return [
            sunbeam_chandlers.ContainerDir(
                "/tftpboot",
                self.charm.service_user,
                self.charm.service_group,
            ),
            sunbeam_chandlers.ContainerDir(
                "/httpboot",
                self.charm.service_user,
                self.charm.service_group,
            ),
        ]

    def get_layer(self) -> dict:
        """Ironic Conductor service layer.

        :returns: pebble layer configuration for the ironic-conductor service
        :rtype: dict
        """
        return {
            "summary": "ironic conductor layer",
            "description": "pebble configuration for ironic-conductor service",
            "services": {
                "ironic-conductor": {
                    "override": "replace",
                    "summary": "Ironic Conductor",
                    "command": "ironic-conductor",
                    "user": "ironic",
                    "group": "ironic",
                }
            },
        }


@sunbeam_tracing.trace_sunbeam_charm
class IronicConductorOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    service_name = "ironic-conductor"

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run the constructor."""
        super().__init__(framework)
        self.framework.observe(
            self.on.set_temp_url_secret_action,
            self._on_set_temp_url_secret_action,
        )
        self.framework.observe(
            self.on.peers_relation_changed, self._on_peer_relation_changed
        )

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "ironic"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "ironic"

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for ironic services."""
        return {"database": "ironic"}

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for the operator."""
        pebble_handlers = [
            IronicConductorPebbleHandler(
                self,
                IRONIC_CONDUCTOR_CONTAINER,
                IRONIC_CONDUCTOR_CONTAINER,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            IronicConductorConfigurationContext(self, "ironic_config")
        )
        return contexts

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/ironic.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/ironic/rootwrap.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/tftpboot/map-file",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/tftpboot/grub/grub.cfg",
                self.service_user,
                self.service_group,
                0o640,
            ),
        ]
        return _cconfigs

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Catchall handler to configure charm services."""
        validated = False
        with sunbeam_guard.guard(self, "Validating configuration"):
            self._validate_default_net_interface()
            self._validate_network_interfaces()
            self._validate_enabled_hw_type()
            self._validate_temp_url_secret()
            validated = True

        if not validated:
            return

        super().configure_charm(event)

    def _validate_default_net_interface(self):
        net_iface = self.config["default-network-interface"]
        if net_iface not in self.enabled_network_interfaces:
            raise sunbeam_guard.BlockedExceptionError(
                "default-network-interface (%s) is not enabled "
                "in enabled-network-interfaces: %s"
                % (net_iface, ", ".join(self.enabled_network_interfaces))
            )

    def _validate_network_interfaces(self):
        for interface in self.enabled_network_interfaces:
            if interface not in VALID_NETWORK_INTERFACES:
                raise sunbeam_guard.BlockedExceptionError(
                    "Network interface %s is not valid. Valid "
                    "interfaces are: %s"
                    % (interface, ", ".join(VALID_NETWORK_INTERFACES))
                )

    def _validate_enabled_hw_type(self):
        hw_types = _HW_TYPES_MAP
        unsupported = []
        for hw_type in self.enabled_hw_types:
            if hw_types.get(hw_type, None) is None:
                unsupported.append(hw_type)

        if len(unsupported) > 0:
            raise sunbeam_guard.BlockedExceptionError(
                "hardware type(s) %s not supported at "
                "this time" % ", ".join(unsupported)
            )

    def _validate_temp_url_secret(self):
        temp_url_secret = self.leader_get("temp_url_secret")
        if not temp_url_secret:
            raise sunbeam_guard.BlockedExceptionError(
                "run set-temp-url-secret action on leader to "
                "enable direct deploy method"
            )

    @property
    def enabled_network_interfaces(self) -> List[str]:
        """Returns list of onfigured enabled-network-interfaces."""
        network_interfaces = self.config.get(
            "enabled-network-interfaces", ""
        ).replace(" ", "")
        return network_interfaces.split(",")

    @property
    def enabled_hw_types(self) -> List[str]:
        """Returns list of onfigured enabled-hw-types."""
        hw_types = self.config.get("enabled-hw-types", "ipmi").replace(" ", "")
        return hw_types.split(",")

    def _on_peer_relation_changed(self, event: RelationChangedEvent):
        """Process peer relation data changed."""
        logger.debug("Processing _on_peer_relation_changed")
        self.configure_charm(event)

    def _on_set_temp_url_secret_action(
        self, event: ops.charm.ActionEvent
    ) -> None:
        """Run set-temp-url-secret action."""
        if not self.model.unit.is_leader():
            event.fail("action must be run on the leader unit.")
            return

        if self.get_mandatory_relations_not_ready(event):
            event.fail(
                "required relations are not yet available, please defer action until "
                "deployment is complete."
            )
            return

        try:
            keystone_session = api_utils.create_keystone_session(
                self.ccreds.interface
            )
        except Exception as e:
            event.fail(f"failed to create keystone session ('{e}')")
            return

        os_cli = api_utils.OSClients(keystone_session)
        if not os_cli.has_swift():
            event.fail(
                "Swift not yet available. Please wait for deployment to finish"
            )
            return

        if not os_cli.has_glance():
            event.fail(
                "Glance not yet available. Please wait for deployment to finish"
            )
            return

        if "swift" not in os_cli.glance_stores:
            event.fail(
                "Glance does not support Swift storage backend. "
                "Please add relation between glance and microceph-ceph-rgw/swift"
            )
            return

        current_secret = self.leader_get("temp_url_secret")
        current_swift_secret = os_cli.get_object_account_properties().get(
            "temp-url-key", None
        )
        if current_secret and current_swift_secret == current_secret:
            # Already stored.
            event.set_results({"output": "Temp URL secret set."})
            return

        # Generate a secret and store it.
        secret = hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()
        os_cli.set_object_account_property("temp-url-key", secret)
        self.leader_set({"temp_url_secret": secret})

        # Manually trigger configure_charm, as peers_relation_changed won't be
        # triggered on the leader unit.
        self.configure_charm(event)

        event.set_results({"output": "Temp URL secret set."})


if __name__ == "__main__":  # pragma: nocover
    ops.main(IronicConductorOperatorCharm)
