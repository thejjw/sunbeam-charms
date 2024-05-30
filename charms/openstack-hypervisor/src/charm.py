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
import ops_sunbeam.core as sunbeam_core
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
from charms.nova_k8s.v0.nova_service import (
    NovaConfigChangedEvent,
    NovaServiceGoneAwayEvent,
)
from cryptography import (
    x509,
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

MIGRATION_BINDING = "migration"
MTLS_USAGES = {x509.OID_SERVER_AUTH, x509.OID_CLIENT_AUTH}


class MTlsCertificatesHandler(sunbeam_rhandlers.TlsCertificatesHandler):
    """Handler for certificates interface."""

    def update_relation_data(self):
        """Update relation outside of relation context."""
        relations = self.model.relations[self.relation_name]
        if len(relations) != 1:
            logger.debug(
                f"Unit has wrong number of {self.relation_name!r} relations."
            )
            return
        relation = relations[0]
        csr = self._get_csr_from_relation_unit_data()
        if not csr:
            self._request_certificates()
            return
        certs = self._get_cert_from_relation_data(csr)
        if "cert" not in certs or not self._has_certificate_mtls_extensions(
            certs["cert"]
        ):
            logger.info(
                "Requesting new certificates, current is missing mTLS extensions."
            )
            relation.data[self.model.unit][
                "certificate_signing_requests"
            ] = "[]"
            self._request_certificates()

    def _has_certificate_mtls_extensions(self, certificate: str) -> bool:
        """Check current certificate has mTLS extensions."""
        cert = x509.load_pem_x509_certificate(certificate.encode())
        for extension in cert.extensions:
            if extension.oid != x509.OID_EXTENDED_KEY_USAGE:
                continue
            extension_oids = {ext.dotted_string for ext in extension.value}
            mtls_oids = {oid.dotted_string for oid in MTLS_USAGES}
            if mtls_oids.issubset(extension_oids):
                return True
        return False

    def _request_certificates(self):
        """Request certificates from remote provider."""
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        from charms.tls_certificates_interface.v1.tls_certificates import (
            generate_csr,
        )

        if self.ready:
            logger.debug("Certificate request already complete.")
            return

        if self.private_key:
            logger.debug("Private key found, requesting certificates")
        else:
            logger.debug("Cannot request certificates, private key not found")
            return

        csr = generate_csr(
            private_key=self.private_key.encode(),
            subject=socket.getfqdn(),
            sans_dns=self.sans_dns,
            sans_ip=self.sans_ips,
            additional_critical_extensions=[
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                x509.ExtendedKeyUsage(MTLS_USAGES),
            ],
        )
        self.certificates.request_certificate_creation(
            certificate_signing_request=csr
        )

    def context(self) -> dict:
        """Certificates context."""
        csr_from_unit = self._get_csr_from_relation_unit_data()
        if not csr_from_unit:
            return {}

        certs = self._get_cert_from_relation_data(csr_from_unit)
        cert = certs["cert"]
        ca_cert = certs["ca"]
        ca_with_intermediates = certs["ca"] + "\n" + "\n".join(certs["chain"])

        ctxt = {
            "key": self.private_key,
            "cert": cert,
            "ca_cert": ca_cert,
            "ca_with_intermediates": ca_with_intermediates,
        }
        return ctxt

    @property
    def ready(self) -> bool:
        """Whether handler ready for use."""
        try:
            return super().ready
        except KeyError:
            return False


class HypervisorOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "hypervisor"
    METADATA_SECRET_KEY = "ovn-metadata-proxy-shared-secret"
    DEFAULT_SECRET_LENGTH = 32

    mandatory_relations = {
        "amqp",
        "identity-credentials",
        "ovsdb-cms",
        "nova-service",
    }

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

    @property
    def migration_address(self) -> Optional[str]:
        """Get address from migration binding."""
        use_binding = self.model.config.get("use-migration-binding")
        if not use_binding:
            return None
        binding = self.model.get_binding(MIGRATION_BINDING)
        if binding is None:
            return None
        address = binding.network.bind_address
        if address is None:
            return None
        return str(address)

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
        if self.can_add_handler("nova-service", handlers):
            self.nova_controller = (
                sunbeam_rhandlers.NovaServiceRequiresHandler(
                    self,
                    "nova-service",
                    self.handle_nova_controller_events,
                    "nova-service" in self.mandatory_relations,
                )
            )
            handlers.append(self.nova_controller)
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
        if self.can_add_handler("certificates", handlers):
            self.certs = MTlsCertificatesHandler(
                self,
                "certificates",
                self.configure_charm,
                sans_dns=self.get_sans_dns(),
                sans_ips=self.get_sans_ips(),
                mandatory="certificates" in self.mandatory_relations,
            )
            handlers.append(self.certs)
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
                if snap_data[k] != hypervisor.get(k, typed=True):
                    new_settings[k] = snap_data[k]
            except snap.SnapError:
                # Trying to retrieve an unset parameter results in a snapError
                # so assume the snap.SnapError means there is missing config
                # that needs setting.
                # Setting a value to None will unset the value from the snap,
                # which will fail if the value was never set.
                if snap_data[k] is not None:
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
                "compute.cacert": base64.b64encode(
                    contexts.certificates.ca_cert.encode()
                ).decode(),
                "compute.cert": base64.b64encode(
                    contexts.certificates.cert.encode()
                ).decode(),
                "compute.key": base64.b64encode(
                    contexts.certificates.key.encode()
                ).decode(),
                "compute.migration-address": self.migration_address
                or config("ip-address")
                or local_ip,
                "compute.resume-on-boot": config("resume-on-boot"),
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
                    contexts.certificates.ca_with_intermediates.encode()
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
        snap_data.update(self._handle_ceph_access(contexts))
        snap_data.update(self._handle_ceilometer_service(contexts))
        snap_data.update(self._handle_nova_service(contexts))
        snap_data.update(self._handle_receive_ca_cert(contexts))

        self.set_snap_data(snap_data)
        self.ensure_services_running()
        self._state.unit_bootstrapped = True

    def _handle_ceph_access(
        self, contexts: sunbeam_core.OPSCharmContexts
    ) -> dict:
        try:
            if contexts.ceph_access.uuid:
                return {
                    "compute.rbd-user": "nova",
                    "compute.rbd-secret-uuid": contexts.ceph_access.uuid,
                    "compute.rbd-key": contexts.ceph_access.key,
                }
        except AttributeError:
            # If the relation has been removed it is probably less disruptive to leave the
            # rbd setting in the snap rather than unsetting them.
            logger.debug("ceph_access relation not integrated")

        return {}

    def _handle_ceilometer_service(
        self, contexts: sunbeam_core.OPSCharmContexts
    ) -> dict:
        try:
            if contexts.ceilometer_service.telemetry_secret:
                return {
                    "telemetry.enable": self.enable_telemetry,
                    "telemetry.publisher-secret": contexts.ceilometer_service.telemetry_secret,
                }
            else:
                return {"telemetry.enable": self.enable_telemetry}
        except AttributeError:
            logger.debug("ceilometer_service relation not integrated")
            return {"telemetry.enable": self.enable_telemetry}

    def _handle_nova_service(
        self, contexts: sunbeam_core.OPSCharmContexts
    ) -> dict:
        try:
            if contexts.nova_service.nova_spiceproxy_url:
                return {
                    "compute.spice-proxy-url": contexts.nova_service.nova_spiceproxy_url,
                }
        except AttributeError as e:
            logger.debug(f"Nova service relation not integrated: {str(e)}")

        return {}

    def _handle_receive_ca_cert(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> dict:
        if (
            hasattr(context.receive_ca_cert, "ca_bundle")
            and context.receive_ca_cert.ca_bundle
        ):
            return {
                "ca.bundle": base64.b64encode(
                    context.receive_ca_cert.ca_bundle.encode()
                ).decode()
            }

        return {"ca.bundle": None}

    def handle_ceilometer_events(self, event: ops.framework.EventBase) -> None:
        """Handle ceilometer events."""
        if isinstance(event, CeilometerConfigChangedEvent):
            self.enable_telemetry = True
            self.configure_charm(event)
        elif isinstance(event, CeilometerServiceGoneAwayEvent):
            self.enable_telemetry = False
            self.configure_charm(event)

    def handle_nova_controller_events(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle nova controller events."""
        if isinstance(event, NovaConfigChangedEvent) or isinstance(
            event, NovaServiceGoneAwayEvent
        ):
            self.configure_charm(event)

    def stop_services(self, relation: Optional[Set[str]]) -> None:
        """Stop services based on relation goneaway event."""
        snap_data = {}
        for relation_ in relation:
            logger.info(f"In stop_services for relation {relation_}")
            if relation_ == "amqp":
                logger.debug("Resetting rabbitmq url")
                snap_data.update({"rabbitmq.url": None})
            elif relation_ == "ovsdb-cms":
                logger.debug("Resetting OVN SB connection")
                snap_data.update({"network.ovn-sb-connection": None})

        if snap_data:
            self.set_snap_data(snap_data)


if __name__ == "__main__":  # pragma: no cover
    main(HypervisorOperatorCharm)
