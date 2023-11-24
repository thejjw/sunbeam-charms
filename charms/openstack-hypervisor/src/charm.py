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


"""OpenStack Hypervisor Operator Charm.

This charm provide hypervisor services as part of an OpenStack deployment
"""

import base64
import logging
import os
import secrets
import socket
import string
from typing import (
    List,
    Optional,
    Set,
)

import charms.operator_libs_linux.v2.snap as snap
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.ovn.relation_handlers as ovn_relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.ceilometer_k8s.v0.ceilometer_service import (
    CeilometerConfigChangedEvent,
    CeilometerServiceGoneAwayEvent,
)
from charms.grafana_agent.v0.cos_agent import (
    COSAgentProvider,
)
from ops.charm import (
    ActionEvent,
)
from ops.main import (
    main,
)
from utils import (
    get_local_ip_by_default_route,
)

logger = logging.getLogger(__name__)


class HypervisorOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "hypervisor"
    METADATA_SECRET_KEY = "ovn-metadata-proxy-shared-secret"
    DEFAULT_SECRET_LENGTH = 32

    mandatory_relations = {"amqp", "identity-credentials", "ovsdb-cms"}

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self._state.set_default(metadata_secret="")
        self.enable_monitoring = self.check_relation_exists("cos-agent")
        # Enable telemetry when ceilometer-service relation is joined
        self.enable_telemetry = self.check_relation_exists(
            "ceilometer-service"
        )
        self.framework.observe(
            self.on.set_hypervisor_local_settings_action,
            self._set_hypervisor_local_settings_action,
        )
        self.framework.observe(
            self.on.cos_agent_relation_joined,
            self._on_cos_agent_relation_joined,
        )
        self.framework.observe(
            self.on.cos_agent_relation_departed,
            self._on_cos_agent_relation_departed,
        )
        self._grafana_agent = COSAgentProvider(
            self,
            metrics_endpoints=[
                {"path": "/metrics", "port": 9177},  # libvirt exporter
                {"path": "/metrics", "port": 9475},  # ovs exporter
                {"path": "/metrics", "port": 12345},  # node exporter
            ],
        )

    def check_relation_exists(self, relation_name: str) -> bool:
        """Check if a relation exists or not."""
        if self.model.get_relation(relation_name):
            return True
        return False

    def _on_cos_agent_relation_joined(self, event: ops.framework.EventBase):
        self.enable_monitoring = True
        self.configure_charm(event)

    def _on_cos_agent_relation_departed(self, event: ops.framework.EventBase):
        self.enable_monitoring = False
        self.configure_charm(event)

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_relation_handlers.OVSDBCMSRequiresHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                "ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        if self.can_add_handler("ceilometer-service", handlers):
            self.ceilometer = (
                sunbeam_rhandlers.CeilometerServiceRequiresHandler(
                    self,
                    "ceilometer-service",
                    self.handle_ceilometer_events,
                    "ceilometer-service" in self.mandatory_relations,
                )
            )
            handlers.append(self.ceilometer)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def _set_hypervisor_local_settings_action(self, event: ActionEvent):
        """Run set_hypervisor_local_settings action."""
        local_settings = [
            "network.external-nic",
            "compute.spice-proxy-address",
            "network.ip-address",
        ]
        new_snap_settings = {}
        for setting in local_settings:
            action_param = setting.split(".")[1]
            if event.params.get(action_param):
                new_snap_settings[setting] = event.params.get(action_param)
        if new_snap_settings:
            self.set_snap_data(new_snap_settings)

    def ensure_services_running(self):
        """Ensure systemd services running."""
        # This should taken care of by the snap
        svcs = [
            "snap.openstack-hypervisor.neutron-ovn-metadata-agent.service",
            "snap.openstack-hypervisor.nova-api-metadata.service",
            "snap.openstack-hypervisor.nova-compute.service",
        ]
        for svc in svcs:
            if os.system(f"systemctl is-active --quiet {svc}") != 0:
                os.system(f"systemctl start {svc}")

    def generate_metadata_secret(self) -> str:
        """Generate a secure secret.

        :param length: length of generated secret
        :type length: int
        :return: string containing the generated secret
        """
        return "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for i in range(self.DEFAULT_SECRET_LENGTH)
        )

    def metadata_secret(self) -> str:
        """Retrieve or set self.METADATA_SECRET_KEY."""
        if self._state.metadata_secret:
            logging.debug("Found metadata secret in local db")
            return self._state.metadata_secret
        else:
            logging.debug("Generating new metadata secret")
            secret = self.generate_metadata_secret()
            self._state.metadata_secret = secret
            return secret

    def set_snap_data(self, snap_data: dict):
        """Set snap data on local snap."""
        cache = snap.SnapCache()
        hypervisor = cache["openstack-hypervisor"]
        new_settings = {}
        for k in sorted(snap_data.keys()):
            try:
                if snap_data[k] != hypervisor.get(k):
                    new_settings[k] = snap_data[k]
            except snap.SnapError:
                # Trying to retrieve an unset parameter results in a snapError
                # so assume the snap.SnapError means there is missing config
                # that needs setting.
                new_settings[k] = snap_data[k]
        if new_settings:
            logger.debug(f"Applying new snap settings {new_settings}")
            hypervisor.set(new_settings, typed=True)
        else:
            logger.debug("Snap settings do not need updating")

    def ensure_snap_present(self):
        """Install snap if it is not already present."""
        config = self.model.config.get
        try:
            cache = snap.SnapCache()
            hypervisor = cache["openstack-hypervisor"]

            if not hypervisor.present:
                hypervisor.ensure(
                    snap.SnapState.Latest, channel=config("snap-channel")
                )
        except snap.SnapError as e:
            logger.error(
                "An exception occurred when installing charmcraft. Reason: %s",
                e.message,
            )

    def configure_unit(self, event) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        config = self.model.config.get
        self.ensure_snap_present()
        local_ip = get_local_ip_by_default_route()
        try:
            contexts = self.contexts()
            sb_connection_strs = list(
                contexts.ovsdb_cms.db_ingress_sb_connection_strs
            )
            if not sb_connection_strs:
                raise AttributeError(name="ovsdb southbound ingress string")

            snap_data = {
                "compute.cpu-mode": "host-model",
                "compute.spice-proxy-address": config("ip-address")
                or local_ip,
                "compute.virt-type": "kvm",
                "credentials.ovn-metadata-proxy-shared-secret": self.metadata_secret(),
                "identity.admin-role": contexts.identity_credentials.admin_role,
                "identity.auth-url": contexts.identity_credentials.internal_endpoint,
                "identity.password": contexts.identity_credentials.password,
                "identity.project-domain-id": contexts.identity_credentials.project_domain_id,
                "identity.project-domain-name": contexts.identity_credentials.project_domain_name,
                "identity.project-name": contexts.identity_credentials.project_name,
                "identity.region-name": contexts.identity_credentials.region,
                "identity.user-domain-id": contexts.identity_credentials.user_domain_id,
                "identity.user-domain-name": contexts.identity_credentials.user_domain_name,
                "identity.username": contexts.identity_credentials.username,
                "logging.debug": config("debug"),
                "network.dns-domain": config("dns-domain"),
                "network.dns-servers": config("dns-servers"),
                "network.enable-gateway": config("enable-gateway"),
                "network.external-bridge": config("external-bridge"),
                "network.external-bridge-address": config(
                    "external-bridge-address"
                )
                or "10.20.20.1/24",
                "network.ip-address": config("ip-address") or local_ip,
                "network.ovn-key": base64.b64encode(
                    contexts.certificates.key.encode()
                ).decode(),
                "network.ovn-cert": base64.b64encode(
                    contexts.certificates.cert.encode()
                ).decode(),
                "network.ovn-cacert": base64.b64encode(
                    contexts.certificates.ca_cert.encode()
                ).decode(),
                "network.ovn-sb-connection": sb_connection_strs[0],
                "network.physnet-name": config("physnet-name"),
                "node.fqdn": socket.getfqdn(),
                "node.ip-address": config("ip-address") or local_ip,
                "rabbitmq.url": contexts.amqp.transport_url,
                "monitoring.enable": self.enable_monitoring,
            }
        except AttributeError as e:
            raise sunbeam_guard.WaitingExceptionError(
                "Data missing: {}".format(e.name)
            )
        # Handle optional config contexts
        try:
            if contexts.ceph_access.uuid:
                snap_data.update(
                    {
                        "compute.rbd-user": "nova",
                        "compute.rbd-secret-uuid": contexts.ceph_access.uuid,
                        "compute.rbd-key": contexts.ceph_access.key,
                    }
                )
        except AttributeError:
            # If the relation has been removed it is probably less disruptive to leave the
            # rbd setting in the snap rather than unsetting them.
            logger.debug("ceph_access relation not integrated")
        try:
            if contexts.ceilometer_service.telemetry_secret:
                snap_data.update(
                    {
                        "telemetry.enable": self.enable_telemetry,
                        "telemetry.publisher-secret": contexts.ceilometer_service.telemetry_secret,
                    }
                )
            else:
                snap_data.update({"telemetry.enable": self.enable_telemetry})
        except AttributeError:
            logger.debug("ceilometer_service relation not integrated")
            snap_data.update({"telemetry.enable": self.enable_telemetry})

        self.set_snap_data(snap_data)
        self.ensure_services_running()
        self._state.unit_bootstrapped = True

    def handle_ceilometer_events(self, event: ops.framework.EventBase) -> None:
        """Handle ceilometer events."""
        if isinstance(event, CeilometerConfigChangedEvent):
            self.enable_telemetry = True
            self.configure_charm(event)
        elif isinstance(event, CeilometerServiceGoneAwayEvent):
            self.enable_telemetry = False
            self.configure_charm(event)

    def stop_services(self, relation: Optional[Set[str]]) -> None:
        """Stop services based on relation goneaway event."""
        snap_data = {}
        for relation_ in relation:
            logger.info(f"In stop_services for relation {relation_}")
            if relation_ == "amqp":
                logger.debug("Resetting rabbitmq url")
                snap_data.update({"rabbitmq.url": ""})
            elif relation_ == "ovsdb-cms":
                logger.debug("Resetting OVN SB connection")
                snap_data.update({"network.ovn-sb-connection": ""})

        if snap_data:
            self.set_snap_data(snap_data)


if __name__ == "__main__":  # pragma: no cover
    main(HypervisorOperatorCharm)
