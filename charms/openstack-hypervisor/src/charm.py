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
import io
import json
import logging
import os
import secrets
import socket
import string
import subprocess
from datetime import (
    datetime,
    timezone,
)
from typing import (
    List,
    Optional,
    Set,
)

import charms.operator_libs_linux.v2.snap as snap
import epa_client
import jsonschema
import ops
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.ovn.relation_handlers as ovn_relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import schemas
import utils
import yaml
from charms.ceilometer_k8s.v0.ceilometer_service import (
    CeilometerConfigChangedEvent,
    CeilometerServiceGoneAwayEvent,
)
from charms.consul_client.v0.consul_notify import (
    ConsulNotifyRequirer,
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
from utils import (
    get_local_ip_by_default_route,
)

logger = logging.getLogger(__name__)

MIGRATION_BINDING = "migration"
DATA_BINDING = "data"
MTLS_USAGES = {x509.OID_SERVER_AUTH, x509.OID_CLIENT_AUTH}
EPA_INFO_PLUG = "epa-info"
EPA_INFO_SLOT = "epa-orchestrator:epa-info"

# Allows overriding DPDK settings.
DPDK_CONFIG_OVERRIDE_PATH = "/etc/sunbeam/dpdk.yaml"

# We'll use separate reservation names for control plane cores and
# datapath cores so that EPA won't override the reservations.
EPA_ALLOCATION_OVS_DPDK_HUGEPAGES = "ovs-dpdk-hugepages"
EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE = "ovs-dpdk-control-plane"
EPA_ALLOCATION_OVS_DPDK_DATAPATH = "ovs-dpdk-datapath"
HYPERVISOR_SNAP_NAME = "openstack-hypervisor"
EVACUATION_UNIX_SOCKET_FILEPATH = "data/shutdown.sock"


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
            self.on.list_gpus_action,
            self._list_gpus_action,
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

        self._epa_client = epa_client.EPAClient()
        self.consul_notify = ConsulNotifyRequirer(self, "consul-notify")
        self.framework.observe(
            self.consul_notify.on.relation_ready,
            self._on_consul_notify_ready,
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

    def _on_consul_notify_ready(self, event: ops.framework.EventBase):
        """Handle the consul-notify relation ready event.

        This event happens when the relation is created or joined.
        """
        logger.debug("Handling consul-notify relation ready event")
        snap_name = HYPERVISOR_SNAP_NAME
        unix_socket_filepath = EVACUATION_UNIX_SOCKET_FILEPATH

        self.consul_notify.set_socket_info(
            snap_name=snap_name,
            unix_socket_filepath=unix_socket_filepath,
        )

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
            "compute.spice-proxy-address",
            "compute.pci-excluded-devices",
            "network.external-nic",
            "network.ip-address",
            "network.ovs-dpdk-ports",
        ]
        new_snap_settings = {}
        for setting in local_settings:
            action_param = setting.split(".")[1]
            if event.params.get(action_param):
                new_snap_settings[setting] = event.params.get(action_param)
        if new_snap_settings:
            self.set_snap_data(new_snap_settings)

        # The OVS pinned cpus and memory need to be reconfigured based on
        # the list of DPDK ports.
        configure_required = "network.ovs-dpdk-ports" in new_snap_settings
        if configure_required:
            logger.info("The OVS DPDK ports changed, reconfiguring charm.")
            self.configure_charm(event)

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

    def _list_gpus_action(self, event: ActionEvent):
        """Run list_gpus action."""
        try:
            stdout = self._hypervisor_cli_cmd("list-gpus --format json")
        except HypervisorError as e:
            event.fail(str(e))
            return

        # cli returns a json dict with keys "gpus"
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

    def _clear_system_ovs_datapaths(self):
        logger.info("Clearing system OVS datapaths.")

        system_ovs_dpctl = "/usr/bin/ovs-dpctl"
        if not os.path.exists(system_ovs_dpctl):
            logger.info(
                "System ovs-dpctl not found, skipped clearing system OVS datapaths."
            )
            return

        result = subprocess.run(
            [system_ovs_dpctl, "dump-dps"],
            capture_output=True,
            text=True,
            check=True,
        )
        datapaths = result.stdout.strip().split("\n")
        for datapath in datapaths:
            datapath = datapath.strip()
            if not datapath:
                continue
            logger.info("Removing OVS datapath: %s", datapath)
            subprocess.run([system_ovs_dpctl, "del-dp", datapath], check=True)

    def _disable_system_ovs(self) -> bool:
        """Disable deb installed OVS.

        OVS crashes if there are multiple conflicting installations, as such
        we are going to mask system OVS services and use the snap based
        installation from "openstack-hypervisor" instead.

        OVS bridges and bonds defined in MAAS will be included in the Netplan
        configuration. We expect Netplan to contain the following in
        order to work with snap based installations:
            https://github.com/canonical/netplan/pull/549

        Note that any configuration defined in the system OVS db that's
        not set in Netplan will be lost.

        Returns a boolean, stating if any changes were made.
        """
        ovs_services = [
            "openvswitch-switch.service",
            "ovs-vswitchd.service",
            "ovsdb-server.service",
            "ovs-record-hostname.service",
        ]

        changes_made = False

        for service_name in ovs_services:
            unit_info = utils.get_systemd_unit_status(service_name)
            if not unit_info:
                logger.debug("%s unit not found.", service_name)
                continue

            if unit_info["active_state"] != "inactive":
                logging.info("Stopping unit: %s", service_name)
                subprocess.run(["systemctl", "stop", service_name], check=True)
                changes_made = True
            else:
                logger.debug("%s unit already stopped.", service_name)

            if unit_info["load_state"] != "masked":
                logging.info("Masking unit: %s", service_name)
                subprocess.run(["systemctl", "mask", service_name], check=True)
                changes_made = True
            else:
                logger.debug("%s unit already masked.", service_name)

        if changes_made:
            self._clear_system_ovs_datapaths()

        return changes_made

    def ensure_snap_present(self):
        """Install snap if it is not already present."""
        config = self.model.config.get

        # If we've just disabled the system OVS services, reapply
        # the netplan configuration to the snap based installation.
        netplan_apply_needed = self._disable_system_ovs()

        try:
            cache = self.get_snap_cache()
            hypervisor = cache["openstack-hypervisor"]

            if not hypervisor.present:
                hypervisor.ensure(
                    snap.SnapState.Latest, channel=config("snap-channel")
                )
                self._connect_to_epa_orchestrator()

                # Netplan expects the "ovs-vsctl" alias in order to pick up the
                # snap installation. The other aliases are there for consistency
                # and convenience.
                hypervisor.alias("ovs-vsctl", "ovs-vsctl")
                hypervisor.alias("ovs-appctl", "ovs-appctl")
                hypervisor.alias("ovs-dpctl", "ovs-dpctl")
                hypervisor.alias("ovs-ofctl", "ovs-ofctl")
        except (snap.SnapError, snap.SnapNotFoundError) as e:
            logger.error(
                "An exception occurred when installing openstack-hypervisor. Reason: %s",
                e.message,
            )

            raise SnapInstallationError(
                "openstack-hypervisor installation failed"
            )

        if netplan_apply_needed:
            logger.info("System OVS services masked, reapplying netplan.")
            subprocess.run(["netplan", "apply"], check=True)
        else:
            logger.debug(
                "No OVS changes made, skipping netplan configuration."
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
            # Temporary manual connection and hook trigger until ownership is transferred
            # to Canonical.
            hypervisor.connect(EPA_INFO_PLUG, slot=EPA_INFO_SLOT)
            logger.info(
                "Successfully connected openstack-hypervisor to epa-orchestrator"
            )
            hypervisor.set(
                {
                    "configure-trigger": int(
                        datetime.now(timezone.utc).timestamp()
                    )
                }
            )
            logger.info(
                "Triggered configure hook via snap set on openstack-hypervisor"
            )
        except snap.SnapError as e:
            logger.error(f"Failed to connect to epa-orchestrator: {e.message}")
            raise

    @functools.cache
    def get_snap_cache(self) -> snap.SnapCache:
        """Return snap cache."""
        return snap.SnapCache()

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
        snap_data.update(self._handle_ovs_dpdk())

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

    def _get_dpdk_settings_override(self) -> dict:
        # Allow overriding DPDK settings through /etc/sunbeam/dpdk.yaml.
        # The field names should match the charm settings.
        #
        # Example:
        #   dpdk:
        #     dpdk-enabled: true
        #     dpdk-memory: 2048
        #     dpdk-datapath-cores: 4
        #     dpdk-controlplane-cores: 4
        if not os.path.exists(DPDK_CONFIG_OVERRIDE_PATH):
            return {}

        with open(DPDK_CONFIG_OVERRIDE_PATH, "r") as f:
            dpdk_config = yaml.safe_load(f)

        schema = yaml.safe_load(
            io.StringIO(schemas.DPDK_CONFIG_OVERRIDE_SCHEMA)
        )
        try:
            jsonschema.validate(dpdk_config, schema)
        except Exception:
            logger.exception("Invalid DPDK configuration.")
            raise

        return dpdk_config["dpdk"]

    def _core_list_to_bitmask(self, core_list: list) -> str:
        """Convert a list of cpu core ids to a bitmask understood by OVS/DPDK."""
        bitmask = 0
        for core in core_list:
            core = int(core)
            # Perform some sanity checks.
            if core < 0 or core > 2048:
                raise ValueError("Invalid core id: %s", core)
            bitmask += 1 << core
        return hex(bitmask)

    def _bitmask_to_core_list(self, core_bitmask: int) -> list[int]:
        """Convert a cpu id bitmask to a list of cpu ids.

        The reverse of _core_list_to_bitmask.
        """
        idx = 0
        cores = []
        while core_bitmask:
            if core_bitmask % 2:
                cores.append(idx)
            idx += 1
            core_bitmask >>= 1
        return cores

    def _get_dpdk_nics_pci_addresses(self) -> list[str]:
        """Obtain the PCI addresses of the nics used with DPDK."""
        cache = self.get_snap_cache()
        hypervisor = cache["openstack-hypervisor"]
        try:
            dpdk_port_mappings = (
                hypervisor.get("internal.dpdk-port-mappings", typed=True) or {}
            )
        except snap.SnapError as e:
            logger.info("Unable to retrieve dpdk port mappings, error: %s", e)
            return []

        if isinstance(dpdk_port_mappings, str):
            dpdk_port_mappings = json.loads(dpdk_port_mappings)

        dpdk_ports = dpdk_port_mappings.get("ports")
        if not dpdk_ports:
            logger.info("No DPDK ports available.")
            return []

        pci_addresses = []
        for interface_name, port_info in dpdk_ports.items():
            logger.info(
                "Found DPDK port: %s -> %s",
                interface_name,
                port_info["pci_address"],
            )
            pci_addresses.append(port_info["pci_address"])

        return pci_addresses

    def _get_dpdk_numa_nodes(self) -> list[int]:
        """Get the list of NUMA nodes that will be used with DPDK.

        We'll use the list of bridged physical ports to determine
        NUMA placement.
        """
        dpdk_iface_pci_addresses = self._get_dpdk_nics_pci_addresses()

        numa_nodes = []
        for pci_address in dpdk_iface_pci_addresses:
            numa_node = utils.get_pci_numa_node(pci_address)
            if numa_node is not None and numa_node not in numa_nodes:
                logger.info(
                    "Detected DPDK port NUMA node: %s -> %s",
                    pci_address,
                    numa_node,
                )
                numa_nodes.append(numa_node)

        if not numa_nodes:
            logging.info(
                "Couldn't detect NUMA nodes based on the network interfaces. "
                "Using NUMA node 0 for DPDK."
            )
            # We could either:
            # * use NUMA node 0 by default
            # * let EPA provide cores and memory from any NUMA node
            #   * we could end up with cores and memory from different NUMA nodes,
            #     impacting performance
            # * spread the allocation across NUMA nodes
            #   * what if the user requested 2GB of memory and we have 4 numa nodes?
            #   * it's easier/safer to just use NUMA node 0 by default.
            numa_nodes = [0]
        return numa_nodes

    def _allocate_dpdk_cores(
        self, allocation_name: str, core_count: int, dpdk_numa_nodes: list[int]
    ) -> list[int]:
        logger.info(
            "Allocating %s cores, name: %s, numa nodes: %s",
            core_count,
            allocation_name,
            dpdk_numa_nodes,
        )

        dpdk_numa_node_count = len(dpdk_numa_nodes)
        if core_count % dpdk_numa_node_count:
            raise Exception(
                f"Core count ({core_count}) not divisible "
                f"by the number of dpdk numa nodes: {dpdk_numa_node_count}."
            )
        cores: list[int] = []

        numa_architecture = utils.get_cpu_numa_architecture()
        numa_nodes = len(numa_architecture)
        for numa_node in range(numa_nodes):
            if numa_node in dpdk_numa_nodes:
                cores += self._epa_client.allocate_cores(
                    allocation_name,
                    core_count // dpdk_numa_node_count,
                    numa_node,
                )
            else:
                # Clear allocations from other NUMA nodes.
                self._epa_client.allocate_cores(
                    allocation_name,
                    -1,
                    numa_node,
                )
        return cores

    def _clear_dpdk_allocations(self):
        logger.info("Clearing DPDK EPA allocations.")
        numa_architecture = utils.get_cpu_numa_architecture()
        numa_nodes = len(numa_architecture)
        for numa_node in range(numa_nodes):
            self._epa_client.allocate_hugepages(
                EPA_ALLOCATION_OVS_DPDK_HUGEPAGES,
                -1,
                1024 * 1024,
                numa_node,
            )
            self._epa_client.allocate_cores(
                EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE,
                -1,
                numa_node,
            )
            self._epa_client.allocate_cores(
                EPA_ALLOCATION_OVS_DPDK_DATAPATH,
                -1,
                numa_node,
            )

    def _handle_ovs_dpdk(self) -> dict:
        dpdk_settings_override = self._get_dpdk_settings_override() or {}
        if dpdk_settings_override:
            logger.info(
                "%s provided, overriding DPDK settings: %s",
                DPDK_CONFIG_OVERRIDE_PATH,
                dpdk_settings_override,
            )

        dpdk_config = lambda key: dpdk_settings_override.get(  # noqa: E731
            key, self.model.config.get(key)
        )

        dpdk_enabled = dpdk_config("dpdk-enabled")
        updates = {"network.ovs-dpdk-enabled": dpdk_enabled}
        if not dpdk_enabled:
            logger.info("DPDK disabled.")
            self._clear_dpdk_allocations()
            return updates

        updates["network.dpdk-driver"] = dpdk_config("dpdk-driver")

        datapath_num_cores = dpdk_config("dpdk-datapath-cores")
        cp_num_cores = dpdk_config("dpdk-control-plane-cores")
        total_memory_mb = dpdk_config("dpdk-memory")

        dpdk_numa_nodes = self._get_dpdk_numa_nodes()
        numa_architecture = utils.get_cpu_numa_architecture()
        all_numa_nodes = list(range(len(numa_architecture)))

        logger.info(
            "DPDK configuration: "
            "control plane cores: %s, datapath cores: %s, dpdk memory (MB): %s, "
            "DPDK numa nodes: %s, total numa nodes: %s",
            cp_num_cores,
            datapath_num_cores,
            total_memory_mb,
            dpdk_numa_nodes,
            len(all_numa_nodes),
        )

        if total_memory_mb:
            memory_mb_per_numa_node = int(total_memory_mb) // len(
                dpdk_numa_nodes
            )
            hugepage_size_kb = 1024 * 1024  # 1GB pages
            hugepages_requested = memory_mb_per_numa_node // 1024

            dpdk_numa_memory = []
            for numa_node in all_numa_nodes:
                if numa_node in dpdk_numa_nodes:
                    self._epa_client.allocate_hugepages(
                        EPA_ALLOCATION_OVS_DPDK_HUGEPAGES,
                        hugepages_requested,
                        hugepage_size_kb,
                        numa_node,
                    )
                    dpdk_numa_memory.append(str(memory_mb_per_numa_node))
                else:
                    # Clear allocation.
                    self._epa_client.allocate_hugepages(
                        EPA_ALLOCATION_OVS_DPDK_HUGEPAGES,
                        -1,
                        1024 * 1024,
                        numa_node,
                    )
                    dpdk_numa_memory.append("0")
            updates["network.ovs-memory"] = ",".join(dpdk_numa_memory)

        if cp_num_cores:
            cp_cores = self._allocate_dpdk_cores(
                EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE,
                cp_num_cores,
                dpdk_numa_nodes,
            )
            updates["network.ovs-lcore-mask"] = self._core_list_to_bitmask(
                cp_cores
            )
        else:
            for numa_node in all_numa_nodes:
                # Clear allocation.
                self._epa_client.allocate_cores(
                    EPA_ALLOCATION_OVS_DPDK_CONTROL_PLANE,
                    -1,
                    numa_node,
                )

        if datapath_num_cores:
            datapath_cores = self._allocate_dpdk_cores(
                EPA_ALLOCATION_OVS_DPDK_DATAPATH,
                datapath_num_cores,
                dpdk_numa_nodes,
            )
            updates["network.ovs-pmd-cpu-mask"] = self._core_list_to_bitmask(
                datapath_cores
            )
        else:
            for numa_node in all_numa_nodes:
                self._epa_client.allocate_cores(
                    EPA_ALLOCATION_OVS_DPDK_DATAPATH,
                    -1,
                    numa_node,
                )

        logger.debug("DPDK snap settings: %s", updates)
        return updates

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
