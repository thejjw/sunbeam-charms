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
from typing import (
    Callable,
    List,
)

import charms.designate_k8s.v0.designate_service as designate_svc
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops.framework import (
    StoredState,
)
from ops.model import (
    BlockedStatus,
)

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class DesignateServiceRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handle external-dns relation on the requires side."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = False,
    ):
        """Create a new external-dns handler.

        Create a new DesignateServiceRequiresHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        :param mandatory: If the relation is mandatory to proceed with
                          configuring charm
        :type mandatory: bool
        """
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> None:
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
class NeutronServerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Handler for interacting with pebble data."""

    def get_layer(self):
        """Neutron server service.

        :returns: pebble service layer configuration for neutron server service
        :rtype: dict
        """
        return {
            "summary": "neutron server layer",
            "description": "pebble configuration for neutron server",
            "services": {
                "neutron-server": {
                    "override": "replace",
                    "summary": "Neutron Server",
                    "command": "neutron-server",
                    "user": "neutron",
                    "group": "neutron",
                }
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
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "neutron",
                0o640,
            ),
        ]


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

        handlers = super().get_relation_handlers(handlers)
        return handlers

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
            "mechanism_drivers": "ovn",
            # Limiting defaults to 2**16 -1 even though geneve vni max is 2**24-1
            # ml2_geneve_allocations will be populated with each vni range
            # which will result in db timeouts if range is 1 - 2**24-1
            # https://opendev.org/openstack/neutron/src/commit/ac1472c8cffe64d32a012c73227595f2f7806de9/neutron/plugins/ml2/drivers/type_tunnel.py#L219-L223
            # This means compute nodes can scale upto 65536
            "vni_ranges": "1:65535",
            "flat_networks": "physnet1",
            "enable_tunneling": "True",
            "local_ip": "127.0.0.1",
            "enable_security_group": "True",
            "max_header_size": "38",
            "ovn_l3_scheduler": "leastloaded",
            "ovn_metadata_enabled": "True",
            "enable_distributed_floating_ip": "False",
            "dns_servers": "",
            "dhcp_default_lease_time": "43200",
            "ovn_dhcp4_global_options": "",
            "ovn_dhcp6_global_options": "",
            "vhost_sock_dir": "/run/libvirt-vhost-user",
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
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "neutron",
                0o640,
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
                "ovsdb-cms" in self.mandatory_relations,
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


if __name__ == "__main__":  # pragma: nocover
    ops.main(NeutronOVNOperatorCharm)
