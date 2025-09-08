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
import functools
import logging
import os
import secrets
import socket
import string
import subprocess
from typing import (
    List,
    Optional,
    Set,
)

import charms.operator_libs_linux.v2.snap as snap
import ops
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.ovn.relation_handlers as ovn_relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
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
from charms.operator_libs_linux.v1.systemd import (
    service_running,
)
from cryptography import (
    x509,
)
from ops.charm import (
    ActionEvent,
)
from utils import (
    get_local_ip_by_default_route,
)

logger = logging.getLogger(__name__)

MIGRATION_BINDING = "migration"
DATA_BINDING = "data"
MTLS_USAGES = {x509.OID_SERVER_AUTH, x509.OID_CLIENT_AUTH}
EPA_INFO_PLUG = "epa-info"
EPA_INFO_SLOT = "epa-orchestrator:epa-info"


class SnapInstallationError(Exception):
    """Custom exception for snap installation failure errors."""


class HypervisorError(Exception):
    """Custom exception for Hypervisor errors."""


@sunbeam_tracing.trace_type
class MTlsCertificatesHandler(sunbeam_rhandlers.TlsCertificatesHandler):
    """Handler for certificates interface."""

    def csrs(self) -> dict[str, bytes]:
        """Return a dict of generated csrs for self.key_names().

        The method calling this method will ensure that all keys have a matching
        csr.
        """
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        from charms.tls_certificates_interface.v3.tls_certificates import (
            generate_csr,
        )

        main_key = self._private_keys.get("main")
        if not main_key:
            return {}

        return {
            "main": generate_csr(
                private_key=main_key.encode(),
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
        }

    def context(self) -> dict:
        """Certificates context."""
        certs = self.interface.get_assigned_certificates()
        if len(certs) != len(self.key_names()):
            return {}
        # openstack-hypervisor only has a main key
        csr = self.store.get_csr("main")
        if csr is None:
            return {}

        main_key = self._private_keys.get("main")
        if main_key is None:
            # this can happen when the relation is removed
            # or unit is departing
            logger.debug("No main key found")
            return {}
        for cert in certs:
            if cert.csr == csr:
                return {
                    "key": main_key,
                    "cert": cert.certificate,
                    "ca_cert": cert.ca,
                    "ca_with_intermediates": cert.ca
                    + "\n"
                    + "\n".join(cert.chain),
                }
        else:
            logger.warning("No certificate found for CSR main")
        return {}


@sunbeam_tracing.trace_sunbeam_charm(extra_types=(snap.SnapCache, snap.Snap))
class HypervisorOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "hypervisor"
    METADATA_SECRET_KEY = "ovn-metadata-proxy-shared-secret"
    DEFAULT_SECRET_LENGTH = 32

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
            self.on.list_nics_action,
            self._list_nics_action,
        )
        self.framework.observe(
            self.on.enable_action,
            self._enable_action,
        )
        self.framework.observe(
            self.on.disable_action,
            self._disable_action,
        )
        self.framework.observe(
            self.on.running_guests_action,
            self._running_guests_action,
        )
        self.framework.observe(
            self.on.list_flavors_action,
            self._list_flavors_action,
        )
        self.framework.observe(
            self.on.install,
            self._on_install,
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

    def _on_install(self, _: ops.InstallEvent):
        """Run install on this unit."""
        with sunbeam_guard.guard(
            self, "Executing install hook event handler", False
        ):
            self.ensure_snap_present()

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

    @property
    def data_address(self) -> Optional[str]:
        """Get address from data binding."""
        use_binding = self.model.config.get("use-data-binding")
        if not use_binding:
            return None
        binding = self.model.get_binding(DATA_BINDING)
        if binding is None:
            return None
        address = binding.network.bind_address
        if address is None:
            return None
        return str(address)

    def _proxy_configs(self) -> dict[str, str]:
        """Return proxy configs."""
        return {
            "HTTPS_PROXY": os.environ.get("JUJU_CHARM_HTTPS_PROXY", ""),
            "HTTP_PROXY": os.environ.get("JUJU_CHARM_HTTP_PROXY", ""),
            "NO_PROXY": os.environ.get("JUJU_CHARM_NO_PROXY", ""),
        }

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

    def get_domain_name_sans(self) -> list[str]:
        """Get Domain names for service."""
        sans = super().get_domain_name_sans()
        sans.append(socket.getfqdn())
        sans.append(socket.gethostname())
        if self.migration_address:
            sans.append(socket.getfqdn(self.migration_address))
        return sans

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
                external_connectivity=self.remote_external_access,
                mandatory="ovsdb-cms" in self.mandatory_relations,
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
        if self.can_add_handler("masakari-service", handlers):
            self.masakari_svc = (
                sunbeam_rhandlers.ServiceReadinessRequiresHandler(
                    self,
                    "masakari-service",
                    self.configure_charm,
                    "masakari-service" in self.mandatory_relations,
                )
            )
            handlers.append(self.masakari_svc)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def _set_hypervisor_local_settings_action(self, event: ActionEvent):
        """Run set_hypervisor_local_settings action."""
        local_settings = [
            "network.external-nic",
            "compute.spice-proxy-address",
            "network.ip-address",
            "compute.pci-excluded-devices",
        ]
        new_snap_settings = {}
        for setting in local_settings:
            action_param = setting.split(".")[1]
            if event.params.get(action_param):
                new_snap_settings[setting] = event.params.get(action_param)
        if new_snap_settings:
            self.set_snap_data(new_snap_settings)

    def _hypervisor_cli_cmd(self, cmd: str):
        """Helper to run cli commands on the snap."""
        cache = self.get_snap_cache()
        hypervisor = cache["openstack-hypervisor"]

        if not hypervisor.present:
            raise HypervisorError("Hypervisor is not installed")

        process = subprocess.run(
            [
                "snap",
                "run",
                "openstack-hypervisor",
                "--verbose",
            ]
            + cmd.split(),
            capture_output=True,
        )

        stderr = process.stderr.decode("utf-8")
        logger.debug("logs: %s", stderr)
        stdout = process.stdout.decode("utf-8")
        logger.debug("stdout: %s", stdout)
        if process.returncode != 0:
            raise HypervisorError(stderr)

        return stdout

    def _list_nics_action(self, event: ActionEvent):
        """Run list_nics action."""
        try:
            stdout = self._hypervisor_cli_cmd("list-nics --format json")
        except HypervisorError as e:
            event.fail(str(e))
            return

        # cli returns a json dict with keys "nics" and "candidate"
        event.set_results({"result": stdout})

    def _enable_action(self, event: ActionEvent):
        """Run enable action."""
        try:
            stdout = self._hypervisor_cli_cmd("hypervisor enable")
        except HypervisorError as e:
            event.fail(str(e))
            return

        event.set_results({"result": stdout})

    def _disable_action(self, event: ActionEvent):
        """Run disable action."""
        try:
            stdout = self._hypervisor_cli_cmd("hypervisor disable")
        except HypervisorError as e:
            event.fail(str(e))
            return

        event.set_results({"result": stdout})

    def _running_guests_action(self, event: ActionEvent):
        """List running openstack guests."""
        try:
            stdout = self._hypervisor_cli_cmd(
                "hypervisor running-guests --format json"
            )
        except HypervisorError as e:
            event.fail(str(e))
            return

        # cli returns a json list
        event.set_results({"result": stdout})

    def _list_flavors_action(self, event: ActionEvent):
        """List compute host capabilities."""
        cache = self.get_snap_cache()
        hypervisor = cache["openstack-hypervisor"]
        try:
            flavors = hypervisor.get("compute.flavors", typed=True)
        except snap.SnapError as e:
            logger.debug(e)
            flavors = ""

        event.set_results({"result": flavors})

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
        cache = self.get_snap_cache()
        hypervisor = cache["openstack-hypervisor"]
        new_settings = {}
        old_settings = hypervisor.get(None, typed=True)
        for key, new_value in snap_data.items():
            group, subkey = key.split(".")
            if (
                old_value := old_settings.get(group, {}).get(subkey)
            ) is not None:
                if old_value != new_value:
                    new_settings[key] = new_value
            # Setting a value to None will unset the value from the snap,
            # which will fail if the value was never set.
            elif new_value is not None:
                new_settings[key] = new_value
        if new_settings:
            logger.debug(f"Applying new snap settings {new_settings}")
            hypervisor.set(new_settings, typed=True)
        else:
            logger.debug("Snap settings do not need updating")

    def ensure_snap_present(self):
        """Install snap if it is not already present."""
        config = self.model.config.get
        try:
            cache = self.get_snap_cache()
            hypervisor = cache["openstack-hypervisor"]

            if not hypervisor.present:
                hypervisor.ensure(
                    snap.SnapState.Latest, channel=config("snap-channel")
                )
                self._connect_to_epa_orchestrator()
        except (snap.SnapError, snap.SnapNotFoundError) as e:
            logger.error(
                "An exception occurred when installing openstack-hypervisor. Reason: %s",
                e.message,
            )

            raise SnapInstallationError(
                "openstack-hypervisor installation failed"
            )

    def _connect_to_epa_orchestrator(self):
        """Connect openstack-hypervisor snap plug to epa-orchestrator snap slot."""
        cache = self.get_snap_cache()
        hypervisor = cache["openstack-hypervisor"]

        try:
            epa_orchestrator = cache["epa-orchestrator"]
            if not epa_orchestrator.present:
                logger.info(
                    "epa-orchestrator not installed, skipping connection"
                )
                return
        except snap.SnapNotFoundError:
            logger.info(
                "epa-orchestrator not found in snap cache, skipping connection"
            )
            return

        try:
            hypervisor.connect(EPA_INFO_PLUG, slot=EPA_INFO_SLOT)
            logger.info(
                "Successfully connected openstack-hypervisor to epa-orchestrator"
            )
        except snap.SnapError as e:
            logger.error(f"Failed to connect to epa-orchestrator: {e.message}")
            raise

    @functools.cache
    def get_snap_cache(self) -> snap.SnapCache:
        """Return snap cache."""
        return snap.SnapCache()

    def check_system_services(self) -> None:
        """Check if system services are in desired state."""
        if service_running("openvswitch-switch.service"):
            logger.error(
                "OpenVSwitch service is running, please stop it before proceeding. "
                "OpenVSwitch is managed by the openstack-hypervisor snap and will "
                "conflict with the snap's operation."
            )
            raise sunbeam_guard.BlockedExceptionError(
                "Breaking: OpenVSwitch service is running on the host."
            )

    def configure_unit(self, event) -> None:
        """Run configuration on this unit."""
        self.check_system_services()
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
                "compute.pci-device-specs": config("pci-device-specs"),
                "credentials.ovn-metadata-proxy-shared-secret": self.metadata_secret(),
                "identity.admin-role": contexts.identity_credentials.admin_role,
                "identity.auth-url": contexts.identity_credentials.internal_endpoint,
                "identity.password": contexts.identity_credentials.password,
                "identity.project-domain-id": contexts.identity_credentials.project_domain_id,
                "identity.project-domain-name": contexts.identity_credentials.project_domain_name,
                "identity.project-id": contexts.identity_credentials.project_id,
                "identity.project-name": contexts.identity_credentials.project_name,
                "identity.region-name": contexts.identity_credentials.region,
                "identity.user-domain-id": contexts.identity_credentials.user_domain_id,
                "identity.user-domain-name": contexts.identity_credentials.user_domain_name,
                "identity.username": contexts.identity_credentials.username,
                "logging.debug": config("debug"),
                "network.dns-servers": config("dns-servers"),
                "network.external-bridge": config("external-bridge"),
                "network.external-bridge-address": config(
                    "external-bridge-address"
                )
                or "10.20.20.1/24",
                "network.ip-address": self.data_address
                or config("ip-address")
                or local_ip,
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
                "sev.reserved-host-memory-mb": config(
                    "reserved-host-memory-mb-for-sev"
                ),
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
        snap_data.update(self._handle_masakari_service(contexts))

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
        config = {}
        try:
            if contexts.nova_service.nova_spiceproxy_url:
                config["compute.spice-proxy-url"] = (
                    contexts.nova_service.nova_spiceproxy_url
                )
            if getattr(contexts.nova_service, "pci_aliases", None):
                config["compute.pci-aliases"] = (
                    contexts.nova_service.pci_aliases
                )
        except AttributeError as e:
            logger.debug(f"Nova service relation not integrated: {str(e)}")

        return config

    def _handle_masakari_service(
        self, contexts: sunbeam_core.OPSCharmContexts
    ) -> dict:
        try:
            return {"masakari.enable": contexts.masakari_service.service_ready}
        except AttributeError:
            logger.info("masakari_service relation not integrated")
            return {"masakari.enable": False}

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
    ops.main(HypervisorOperatorCharm)
