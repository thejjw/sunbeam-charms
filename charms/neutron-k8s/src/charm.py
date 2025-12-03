#!/usr/bin/env python3
#
# Copyright 2022 Canonical Ltd.
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

"""Neutron Operator Charm.

This charm provide Neutron services as part of an OpenStack deployment
"""

import logging
import re
import tomllib
from typing import (
    List,
)

import charms.designate_k8s.v0.designate_service as designate_svc
import charms.neutron_k8s.v0.switch_config as switch_config
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.templating as sunbeam_templating
import ops_sunbeam.tracing as sunbeam_tracing
from ops.framework import (
    StoredState,
)
from ops.model import (
    BlockedStatus,
)

logger = logging.getLogger(__name__)

BAREMETAL_SWITCH_CONFIG_RELATION = "baremetal-switch-config"
ML2_BAREMETAL_CONF = (
    "/etc/neutron/plugins/ml2/ml2_conf_networking_baremetal.ini"
)

GENERIC_SWITCH_CONFIG_RELATION = "generic-switch-config"
ML2_GENERIC_CONF = "/etc/neutron/plugins/ml2/ml2_conf_genericswitch.ini"

IRONIC_API_RELATION = "ironic-api"
IRONIC_AGENT_CONF = "/etc/neutron/plugins/ml2/ironic_neutron_agent.ini"
IRONIC_AGENT = "ironic-neutron-agent"


@sunbeam_tracing.trace_type
class ML2Context(sunbeam_ctxts.ConfigContext):
    """ML2 configuration."""

    def context(self) -> dict:
        """Configuration context."""
        return {
            "mechanism_drivers": ",".join(self.charm.mechanism_drivers),
        }


@sunbeam_tracing.trace_type
class BaremetalConfigContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set baremetal parameters."""

    def context(self) -> dict:
        """Generate configuration information for baremetal config."""
        configs = []
        enabled_devices = []
        additional_files = {}
        for config in self.charm.baremetal_config.interface.switch_configs:
            conf = config.get("conf", "")
            configs.append(conf)

            try:
                config_toml = tomllib.loads(conf)
                enabled_devices.extend(config_toml.keys())
            except tomllib.TOMLDecodeError as ex:
                logger.error("Could not decode TOML. Error: %s", ex)
                raise sunbeam_guard.BlockedExceptionError(
                    "Invalid content in secret baremetal-switch-config secret. Check logs."
                )

            for name in config_toml.keys():
                section = config_toml[name]
                key_filename = section.get("key_filename")
                if not key_filename:
                    continue

                dict_key = key_filename.split("/")[-1].replace("_", "-")
                if dict_key not in config:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Missing '{dict_key}' additional file from baremetal-switch-config secret."
                    )

                additional_files[key_filename] = config[dict_key]

        ctxt = {
            "enabled_devices": ",".join(enabled_devices),
            "configs": configs,
            "additional_files": additional_files,
        }

        return ctxt


@sunbeam_tracing.trace_type
class GenericConfigContext(sunbeam_ctxts.ConfigContext):
    """Configuration context to set generic parameters."""

    def context(self) -> dict:
        """Generate configuration information for generic config."""
        configs = []
        additional_files = {}
        for config in self.charm.generic_config.interface.switch_configs:
            conf = config.get("conf", "")
            configs.append(conf)

            try:
                config_toml = tomllib.loads(conf)
            except tomllib.TOMLDecodeError as ex:
                logger.error("Could not decode TOML. Error: %s", ex)
                raise sunbeam_guard.BlockedExceptionError(
                    "Invalid content in secret generic-switch-config secret. Check logs."
                )

            for name in config_toml.keys():
                section = config_toml[name]
                key_file = section.get("key_file")
                if not key_file:
                    continue

                dict_key = key_file.split("/")[-1].replace("_", "-")
                if dict_key not in config:
                    raise sunbeam_guard.BlockedExceptionError(
                        f"Missing '{dict_key}' additional file from generic-switch-config secret."
                    )

                additional_files[key_file] = config[dict_key]

        ctxt = {
            "configs": configs,
            "additional_files": additional_files,
        }

        return ctxt


@sunbeam_tracing.trace_type
class DesignateServiceRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handle external-dns relation on the requires side."""

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for external-dns service relation."""
        logger.debug("Setting up Designate service event handler")
        svc = sunbeam_tracing.trace_type(
            designate_svc.DesignateServiceRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.endpoint_changed,
            self._on_endpoint_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_goneaway,
        )
        return svc

    def _on_endpoint_changed(self, event: ops.framework.EventBase) -> None:
        """Handle endpoint_changed  event."""
        logger.debug(
            "Designate service provider endpoint changed event received"
        )
        self.callback_f(event)

    def _on_goneaway(self, event: ops.framework.EventBase) -> None:
        """Handle gone_away  event."""
        logger.debug("Designate service relation is departed/broken")
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.endpoint)
        except (AttributeError, KeyError):
            return False


@sunbeam_tracing.trace_type
class SwitchConfigRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handles the switch-config relation on the requires side."""

    interface = "switch_config.SwitchConfigRequires"

    def setup_event_handler(self):
        """Configure event handlers for the switch-config relation."""
        logger.debug(f"Setting up {self.relation_name} event handler")
        handler = sunbeam_tracing.trace_type(
            switch_config.SwitchConfigRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            handler.on.switch_config_connected,
            self._switch_config_connected,
        )
        self.framework.observe(
            handler.on.switch_config_goneaway,
            self._switch_config_goneaway,
        )
        return handler

    def _switch_config_connected(self, event) -> None:
        """Handles switch-config connected events."""
        self.callback_f(event)

    def _switch_config_goneaway(self, event) -> None:
        """Handles switch-config goneaway events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Interface ready for use."""
        relations = self.model.relations[self.relation_name]
        if not relations:
            return False

        relation = relations[0]
        if not relation.data[relation.app].get(switch_config.SWITCH_CONFIG):
            return False

        return True


@sunbeam_tracing.trace_type
class NeutronServerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Handler for interacting with pebble data."""

    def get_layer(self):
        """Neutron server service.

        :returns: pebble service layer configuration for neutron server service
        :rtype: dict
        """
        neutron_command = [
            "neutron-server",
            "--config-dir",
            "/etc/neutron",
            "--config-file",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
        ]
        if self.charm.baremetal_config.ready:
            neutron_command.extend(["--config-file", ML2_BAREMETAL_CONF])
        if self.charm.generic_config.ready:
            neutron_command.extend(["--config-file", ML2_GENERIC_CONF])

        return {
            "summary": "neutron server layer",
            "description": "pebble configuration for neutron server",
            "services": {
                "neutron-server": {
                    "override": "replace",
                    "summary": "Neutron Server",
                    "command": " ".join(neutron_command),
                    "user": "neutron",
                    "group": "neutron",
                },
                IRONIC_AGENT: {
                    "override": "replace",
                    "summary": "Neutron Ironic Agent",
                    "command": f"ironic-neutron-agent --config-dir /etc/neutron --config-file {IRONIC_AGENT_CONF}",
                    "user": "neutron",
                    "group": "neutron",
                },
            },
        }

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for neutron server
                  service
        :rtype: dict
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "http": {"url": self.charm.healthcheck_http_url},
                },
            }
        }

    def default_container_configs(self):
        """Base container configs."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/neutron.conf", "neutron", "neutron"
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/api-paste.ini", "neutron", "neutron"
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/api_audit_map.conf", "root", "neutron"
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "neutron",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                ML2_BAREMETAL_CONF,
                "root",
                "neutron",
            ),
            sunbeam_core.ContainerConfigFile(
                ML2_GENERIC_CONF,
                "root",
                "neutron",
            ),
            sunbeam_core.ContainerConfigFile(
                IRONIC_AGENT_CONF,
                "root",
                "neutron",
            ),
        ]

    def write_config(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> list[str]:
        """Write configuration files into the container.

        Write self.container_configs into container if there contents
        have changed.

        Additionally, write the additional files from the baremetal-switch-config
        relation, if any.

        :return: List of files that were updated
        :rtype: List
        """
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug("Container not ready")
            return []

        baremetal_context = context.baremetal.context()
        additional_files = baremetal_context.get("additional_files", {})

        generic_context = context.generic.context()
        additional_files.update(generic_context.get("additional_files", {}))

        updated_files = []
        for filepath, contents in additional_files.items():
            config_file = sunbeam_core.ContainerConfigFile(
                filepath,
                "root",
                "neutron",
                0o640,
            )

            updated = sunbeam_templating.sidecar_config_write(
                container,
                config_file,
                contents,
            )
            if updated:
                updated_files.append(filepath)

        files = super().write_config(context)
        updated_files.extend(files)

        return updated_files

    def start_service(self, restart: bool = True) -> None:
        """Check and start services in container.

        :param restart: Whether to stop services before starting them.
        """
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(
                f"{self.container_name} container is not ready. "
                "Cannot start service."
            )
            return

        plan = container.get_plan()
        layer = self.get_layer()

        if layer["services"] != plan.services:
            container.add_layer(self.service_name, layer, combine=True)

        self.start_all(restart=restart)

    def start_all(
        self,
        restart: bool = True,
    ) -> None:
        """Start services in container.

        :param restart: Whether to stop services before starting them.
        """
        # NOTE(claudiub): Despite the name, we only start the
        # ironic-neutron-agent only if the ironic relation is set, otherwise
        # we stop it.
        container = self.charm.unit.get_container(self.container_name)
        if not container.can_connect():
            logger.debug(
                f"Container {self.container_name} not ready, deferring restart"
            )
            return

        services = container.get_services()
        service_names = list(services.keys())

        ironic_rel = self.model.relations[IRONIC_API_RELATION]
        if not ironic_rel and IRONIC_AGENT in service_names:
            service_names.remove(IRONIC_AGENT)
            container.stop(IRONIC_AGENT)

        for service_name in service_names:
            service = services.get(service_name)
            if not service.is_running():
                logger.debug(
                    f"Starting {service_name} in {self.container_name}"
                )
                container.start(service_name)
                self._reset_files_changed()
                continue

            if restart:
                logger.debug(
                    f"Restarting {service_name} in {self.container_name}"
                )
                self._restart_methods.get(service_name, self._restart_service)(
                    container, service_name
                )
                self._reset_files_changed()

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if not self.pebble_ready:
            return False

        container = self.charm.unit.get_container(self.container_name)
        services = container.get_services()
        service_names = list(services.keys())

        ironic_rel = self.model.relations[IRONIC_API_RELATION]
        if not ironic_rel and IRONIC_AGENT in service_names:
            service_names.remove(IRONIC_AGENT)

        return all(services.get(name).is_running() for name in service_names)


class NeutronOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "neutron-server"
    # Remove wsgi_admin_script and wsgi_admin_script after aso fix
    wsgi_admin_script = ""
    wsgi_public_script = ""

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "neutron",
            "neutron-db-manage",
            "--config-file",
            "/etc/neutron/neutron.conf",
            "--config-file",
            "/etc/neutron/plugins/ml2/ml2_conf.ini",
            "upgrade",
            "head",
        ]
    ]
    db_sync_timeout = 480

    def check_configuration(self, event: ops.EventBase):
        """Check a configuration key is correct."""
        try:
            self._validate_domain()
            self._validate_ptr_zone_prefix_size()
        except ValueError as e:
            raise sunbeam_guard.BlockedExceptionError(str(e)) from e

    def _validate_domain(self):
        """Check given domain is valid."""
        domain = self.config.get("dns-domain")
        if not domain:
            raise ValueError("dns-domain cannot be empty")

        if len(domain) > 253:
            raise ValueError(
                "A full name cannot be longer than 253 characters (trailing dot included)"
            )

        if not domain.endswith("."):
            raise ValueError("A domain name must have a trailing dot (.)")

        labels = domain.split(".")

        if len(labels) == 1:
            raise ValueError(
                "A domain name must have at least one label and a trailing dot,"
                " or two labels separated by a dot"
            )

        if domain.endswith("."):
            # strip trailing dot
            del labels[-1]

        label_regex = re.compile(r"^[a-z0-9-]*$", re.IGNORECASE)

        for label in labels:
            if not 1 < len(label) < 63:
                raise ValueError(
                    "A label in a domain cannot be empty or longer than 63 characters"
                )

            if label.startswith("-") or label.endswith("-"):
                raise ValueError(
                    "A label in a domain cannot start or end with a hyphen (-)"
                )

            if label_regex.match(label) is None:
                raise ValueError(
                    "A label in a domain can only contain alphanumeric characters"
                    " and hyphens (-)"
                )

    def _validate_ptr_zone_prefix_size(self):
        """Check given ptr zone prefix size is valid."""
        ipv4_prefix_size = self.config.get("ipv4-ptr-zone-prefix-size")
        valid_ipv4_prefix_size = (8 <= ipv4_prefix_size <= 24) and (
            ipv4_prefix_size % 8
        ) == 0
        if not valid_ipv4_prefix_size:
            raise ValueError(
                "Invalid ipv4-ptr-zone-prefix-size. Value should be between 8 - 24 and multiple of 8"
            )

        ipv6_prefix_size = self.config.get("ipv6-ptr-zone-prefix-size")
        valid_ipv6_prefix_size = (4 <= ipv6_prefix_size <= 124) and (
            ipv6_prefix_size % 4
        ) == 0
        if not valid_ipv6_prefix_size:
            raise ValueError(
                "Invalid ipv6-ptr-zone-prefix-size. Value should be between 4 - 124 and multiple of 4"
            )

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_configuration(event)
        return super().configure_unit(event)

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("external-dns", handlers):
            self.external_dns = DesignateServiceRequiresHandler(
                self,
                "external-dns",
                self.configure_charm,
                "external-dns" in self.mandatory_relations,
            )
            handlers.append(self.external_dns)

        if self.can_add_handler(BAREMETAL_SWITCH_CONFIG_RELATION, handlers):
            self.baremetal_config = SwitchConfigRequiresHandler(
                self,
                BAREMETAL_SWITCH_CONFIG_RELATION,
                self.configure_charm,
                BAREMETAL_SWITCH_CONFIG_RELATION in self.mandatory_relations,
            )
            handlers.append(self.baremetal_config)

        if self.can_add_handler(GENERIC_SWITCH_CONFIG_RELATION, handlers):
            self.generic_config = SwitchConfigRequiresHandler(
                self,
                GENERIC_SWITCH_CONFIG_RELATION,
                self.configure_charm,
                GENERIC_SWITCH_CONFIG_RELATION in self.mandatory_relations,
            )
            handlers.append(self.generic_config)

        if self.can_add_handler(IRONIC_API_RELATION, handlers):
            self.ironic_svc = (
                sunbeam_rhandlers.ServiceReadinessRequiresHandler(
                    self,
                    IRONIC_API_RELATION,
                    self.handle_ironic_readiness,
                    IRONIC_API_RELATION in self.mandatory_relations,
                )
            )
            handlers.append(self.ironic_svc)

        handlers = super().get_relation_handlers(handlers)
        return handlers

    def handle_ironic_readiness(self, event: ops.EventBase):
        """Handle ironic-api service readiness callback."""
        self.configure_charm(event)

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            NeutronServerPebbleHandler(
                self,
                "neutron-server",
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    @property
    def service_endpoints(self):
        """Neutron service endpoint description."""
        return [
            {
                "service_name": "neutron",
                "type": "network",
                "description": "OpenStack Networking",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Public ingress port."""
        return 9696

    @property
    def ingress_healthcheck_path(self):
        """Healthcheck path for ingress relation."""
        return "/healthcheck"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "neutron"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "neutron"

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/neutron/neutron.conf"

    @property
    def config_contexts(self) -> list[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(ML2Context(self, "ml2"))
        contexts.append(BaremetalConfigContext(self, "baremetal"))
        contexts.append(GenericConfigContext(self, "generic"))
        return contexts

    @property
    def mechanism_drivers(self) -> List[str]:
        """Returns the list of ML2 mechanism drivers used."""
        ironic_rel = self.model.relations[IRONIC_API_RELATION]
        if ironic_rel:
            return ["baremetal"]

        return []


# Neutron OVN Specific Code


@sunbeam_tracing.trace_type
class OVNContext(sunbeam_ctxts.ConfigContext):
    """OVN configuration."""

    def context(self) -> dict:
        """Configuration context."""
        return {
            "extension_drivers": "port_security,qos,dns_domain_ports,port_forwarding,uplink_status_propagation",
            "type_drivers": "geneve,vlan,flat",
            "tenant_network_types": "geneve,vlan,flat",
            # Limiting defaults to 2**16 -1 even though geneve vni max is 2**24-1
            # ml2_geneve_allocations will be populated with each vni range
            # which will result in db timeouts if range is 1 - 2**24-1
            # https://opendev.org/openstack/neutron/src/commit/ac1472c8cffe64d32a012c73227595f2f7806de9/neutron/plugins/ml2/drivers/type_tunnel.py#L219-L223
            # This means compute nodes can scale upto 65536
            "vni_ranges": "1:65535",
            "flat_networks": "*",
            "enable_tunneling": "True",
            "local_ip": "127.0.0.1",
            "enable_security_group": "True",
            "max_header_size": "38",
            "ovn_l3_scheduler": "leastloaded",
            "ovn_metadata_enabled": "True",
            "dns_servers": "",
            "dhcp_default_lease_time": "43200",
            "ovn_dhcp4_global_options": "",
            "ovn_dhcp6_global_options": "",
            "vhost_sock_dir": "/var/snap/openstack-hypervisor/common/run/libvirt",
            "ovn_key": "/etc/neutron/plugins/ml2/key_host",
            "ovn_cert": "/etc/neutron/plugins/ml2/cert_host",
            "ovn_ca_cert": "/etc/neutron/plugins/ml2/neutron-ovn.crt",
        }


@sunbeam_tracing.trace_type
class NeutronServerOVNPebbleHandler(NeutronServerPebbleHandler):
    """Handler for interacting with neutron container."""

    def default_container_configs(self):
        """Neutron container configs."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/neutron.conf", "root", "neutron", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/plugins/ml2/key_host", "root", "neutron", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/plugins/ml2/cert_host", "root", "neutron", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/plugins/ml2/neutron-ovn.crt",
                "root",
                "neutron",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/plugins/ml2/ml2_conf.ini",
                "root",
                "neutron",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/api-paste.ini", "root", "neutron", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/neutron/api_audit_map.conf", "root", "neutron", 0o640
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "neutron",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                ML2_BAREMETAL_CONF,
                "root",
                "neutron",
            ),
            sunbeam_core.ContainerConfigFile(
                ML2_GENERIC_CONF,
                "root",
                "neutron",
            ),
            sunbeam_core.ContainerConfigFile(
                IRONIC_AGENT_CONF,
                "root",
                "neutron",
            ),
        ]


@sunbeam_tracing.trace_sunbeam_charm
class NeutronOVNOperatorCharm(NeutronOperatorCharm):
    """Neutron charm class for OVN."""

    @property
    def config_contexts(self) -> list[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(OVNContext(self, "ovn"))
        return contexts

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            NeutronServerOVNPebbleHandler(
                self,
                "neutron-server",
                self.service_name,
                [],
                self.template_dir,
                self.configure_charm,
            )
        ]

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_rhandlers.OVSDBCMSRequiresHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                external_connectivity=self.remote_external_access,
                mandatory="ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    @sunbeam_job_ctrl.run_once_per_unit("post-db-sync-restart")
    def _post_db_sync_restart(self) -> None:
        # If neutron-server is running prior to the db-sync the
        # hash ring job can wedge communication with ovn so restart
        # neutron-server. Note that the run_once_per_unit decorator
        # ensure this is only run once.
        handler = self.get_named_pebble_handler("neutron-server")
        logger.debug("Restarting neutron-server after db sync")
        handler.start_all(restart=True)

    @sunbeam_job_ctrl.run_once_per_unit("db-sync")
    def run_db_sync(self) -> None:
        """Run db sync and restart neutron-server."""
        super().run_db_sync()
        self._post_db_sync_restart()

    def configure_app_non_leader(self, event):
        """Setup steps for a non-leader after leader has bootstrapped."""
        if not self.bootstrapped:
            raise sunbeam_guard.WaitingExceptionError("Leader not ready")
        self._post_db_sync_restart()

    @property
    def mechanism_drivers(self) -> List[str]:
        """Returns the list of ML2 mechanism drivers used."""
        drivers = super().mechanism_drivers
        drivers.extend(["sriovnicswitch", "ovn"])
        return drivers


if __name__ == "__main__":  # pragma: nocover
    ops.main(NeutronOVNOperatorCharm)
