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
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
from ipaddress import (
    IPv4Address,
    IPv6Address,
)
from typing import (
    List,
    Mapping,
    Union,
)

import ops
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.ovn.charm as ovn_charm
import ops_sunbeam.ovn.config_contexts as ovn_ctxts
import ops_sunbeam.ovn.container_handlers as ovn_chandlers
import ops_sunbeam.ovn.relation_handlers as ovn_relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from lightkube.models.core_v1 import (
    ServicePort,
)
from ops_sunbeam.k8s_resource_handlers import (
    KubernetesLoadBalancerHandler,
)

logger = logging.getLogger(__name__)

OVSDB_SERVER = "ovsdb-server"


@sunbeam_tracing.trace_type
class OVNRelayPebbleHandler(ovn_chandlers.OVNPebbleHandler):
    """Handler for OVN Relay container."""

    @property
    def wrapper_script(self):
        """Wrapper script for managing OVN service."""
        return "/root/ovn-relay-wrapper.sh"

    @property
    def status_command(self):
        """Status command for container."""
        return "/usr/share/ovn/scripts/ovn-ctl status_ovsdb"

    @property
    def service_description(self):
        """Description of service."""
        return "OVN Relay"

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Initialise service ready for use.

        Write configuration files to the container and record
        that service is ready for us.

        NOTE: Override default to services being automatically started
        """
        self.setup_dirs()
        changes = self.write_config(context)
        self.files_changed(changes)
        self.start_service()


@sunbeam_tracing.trace_sunbeam_charm
class OVNRelayOperatorCharm(ovn_charm.OSBaseOVNOperatorCharm):
    """Charm the service."""

    def __init__(self, framework):
        super().__init__(framework)
        service_ports = [ServicePort(6642, name="southbound")]
        self.lb_handler = KubernetesLoadBalancerHandler(
            self,
            service_ports,
            refresh_event=[self.on.install, self.on.config_changed],
        )
        self.unit.set_ports(6642)

        self.framework.observe(
            self.on.get_southbound_db_url_action,
            self._get_southbound_db_url_action,
        )
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

    def _on_upgrade_charm(self, event: ops.framework.EventBase):
        """Handle the upgrade charm event."""
        logger.info("Handling upgrade-charm event")
        self.certs.validate_and_regenerate_certificates_if_needed(
            self.get_tls_certificate_requests()
        )

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        self.ovsdb_cms = ovn_relation_handlers.OVSDBCMSRequiresHandler(
            self,
            "ovsdb-cms",
            self.configure_charm,
            external_connectivity=self.remote_external_access,
            mandatory=True,
        )
        handlers.append(self.ovsdb_cms)
        self.ovsdb_cms_relay = ovn_relation_handlers.OVSDBCMSProvidesHandler(
            self,
            "ovsdb-cms-relay",
            self.configure_charm,
            loadbalancer_address=self.lb_handler.get_loadbalancer_ip(),
            mandatory=False,
        )
        handlers.append(self.ovsdb_cms_relay)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def _get_southbound_db_url_action(self, event):
        event.set_results({"url": self.southbound_db_url})

    @property
    def ingress_address(self) -> Union[str, IPv4Address, IPv6Address]:
        """Network IP address for access to the OVN relay service."""
        return (
            self.lb_handler.get_loadbalancer_ip()
            or self.model.get_binding(
                "ovsdb-cms-relay"
            ).network.ingress_addresses[0]
        )

    @property
    def southbound_db_url(self) -> str:
        """Full connection URL for Southbound DB relay."""
        return f"ssl:{self.ingress_address}:6642"

    def get_pebble_handlers(self):
        """Return pebble handler for container."""
        pebble_handlers = [
            OVNRelayPebbleHandler(
                self,
                OVSDB_SERVER,
                "ovn-relay",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]
        return pebble_handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(ovn_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Return empty dict as no mysql databases are
        required.
        """
        return {}

    def get_sans_ips(self) -> frozenset[str]:
        """Return list of SANs for the certificate."""
        sans_ips = list(super().get_sans_ips())
        lb_address = self.lb_handler.get_loadbalancer_ip()
        if lb_address and lb_address not in sans_ips:
            sans_ips.append(lb_address)
        return frozenset(sans_ips)


if __name__ == "__main__":  # pragma: nocover
    ops.main(OVNRelayOperatorCharm)
