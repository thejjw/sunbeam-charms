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

"""Octavia Operator Charm.

This charm provide Octavia services as part of an OpenStack deployment
"""

import hashlib
import json
import logging
import re
import secrets
from typing import (
    List,
)

import charms.keystone_k8s.v0.identity_resource as identity_resource
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import tenacity
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    Mode,
    TLSCertificatesRequiresV4,
)
from lightkube.core.client import (
    Client,
)
from lightkube.core.exceptions import (
    ApiError,
)
from lightkube.resources.apps_v1 import (
    DaemonSet,
    StatefulSet,
)
from lightkube.resources.core_v1 import (
    Pod,
)
from lightkube.types import (
    PatchType,
)
from ops.framework import (
    BoundEvent,
    Object,
    StoredState,
)
from ops.model import (
    ModelError,
)

logger = logging.getLogger(__name__)
OCTAVIA_API_CONTAINER = "octavia-api"
OCTAVIA_CONTROLLER_CONTAINER = "octavia-controller"
OCTAVIA_AGENT_SOCKET_DIR = "/var/run/octavia"
# Services within octavia-controller that are always running
_CONTROLLER_ALWAYS_ON_SERVICES = (
    "octavia-driver-agent",
    "octavia-housekeeping",
)
# Services within octavia-controller that only run when Amphora is configured
_CONTROLLER_AMPHORA_SERVICES = ("octavia-health-manager", "octavia-worker")
OCTAVIA_HEALTH_MANAGER_PORT = 5555
OCTAVIA_HEARTBEAT_KEY = "heartbeat-key"

# Peer app-databag keys used to share Amphora cert material with non-leader units.
# Public cert data (PEM strings) is stored directly in the databag; private
# keys are stored in a Juju app secret whose ID is kept in the databag.
AMPHORA_ISSUING_CACERT_PEER_KEY = "amphora-issuing-cacert"
AMPHORA_ISSUING_CA_ROOT_PEER_KEY = "amphora-issuing-ca-root"
AMPHORA_CONTROLLER_CACERT_PEER_KEY = "amphora-controller-cacert"
AMPHORA_CONTROLLER_CERT_PEER_KEY = "amphora-controller-cert"
AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY = "amphora-certs-pk-secret-id"
AMPHORA_CERTS_PRIVATE_KEYS_LABEL = "octavia-amphora-certs-private-keys"

_VALID_TOPOLOGY = frozenset({"SINGLE", "ACTIVE_STANDBY"})
# "auto" is a charm-internal value: it maps to soft-anti-affinity for
# ACTIVE_STANDBY and disables anti-affinity for SINGLE topology.
_VALID_ANTI_AFFINITY = frozenset(
    {"anti-affinity", "soft-anti-affinity", "auto", "disable"}
)
_VALID_LOG_PROTOCOL = frozenset({"UDP", "TCP"})

_CA_BUNDLE_PEM = "/usr/local/share/ca-certificates/ca-bundle.pem"
_CA_BUNDLE_CRT = "/usr/local/share/ca-certificates/ca-bundle.crt"


def _push_ca_bundle_and_update(
    container: ops.Container, content: bytes
) -> None:
    """Push ca-bundle.crt and run update-ca-certificates if content changed."""
    try:
        existing = container.pull(_CA_BUNDLE_CRT, encoding=None).read()
    except ops.pebble.PathError:
        existing = None
    if existing == content:
        logger.debug(
            "ca-bundle.crt is up to date; skipping update-ca-certificates"
        )
        return
    container.push(_CA_BUNDLE_CRT, content, make_dirs=True)
    process = container.exec(["update-ca-certificates"], timeout=60)
    out, warnings = process.wait_output()
    if out:
        logger.debug("update-ca-certificates: %s", out.strip())
    if warnings:
        for line in warnings.splitlines():
            logger.warning("update-ca-certificates warn: %s", line.strip())


def _run_update_ca_certificates(container: ops.Container) -> None:
    """Copy ca-bundle.pem to ca-bundle.crt and run update-ca-certificates.

    Workaround for LP: #2147695 — update-ca-certificates only processes
    .crt files, so the .pem bundle written by the charm is not picked up
    by the system trust store until it is also present as a .crt file.

    Only runs when ca-bundle.pem is present (i.e. receive-ca-cert relation
    has provided a CA bundle).

    TODO (hemanth): This workaround is required only for Ubuntu Octavia Caracal
    package with version < 14.0.1. Once the Ubuntu Octavia package is updated
    to 14.0.1 the workaround can be removed.
    """
    try:
        files = container.list_files(
            "/usr/local/share/ca-certificates", pattern="ca-bundle.pem"
        )
    except ops.pebble.APIError:
        logger.debug(
            "Could not list ca-certificates directory; skipping update-ca-certificates"
        )
        return
    if not files:
        logger.debug(
            "ca-bundle.pem not present; skipping update-ca-certificates"
        )
        return
    try:
        content = container.pull(_CA_BUNDLE_PEM, encoding=None).read()
        _push_ca_bundle_and_update(container, content)
    except ops.pebble.ExecError:
        logger.exception("Failed to run update-ca-certificates")


@sunbeam_tracing.trace_type
class OctaviaWSGIPebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """WSGIPebbleHandler for octavia-api that skips redundant pebble RPCs.

    The base WSGIPebbleHandler.init_service() calls configure_container()
    (which calls write_config()) and then write_config() a second time.
    combine_containers() already called configure_container() before
    init_container_services() is invoked, so both of those calls are
    duplicates.  Overriding here avoids ~2× the pebble pull() RPCs.
    """

    def configure_container(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> None:
        """Write configuration files and update CA certificates."""
        super().configure_container(context)
        if self.pebble_ready:
            container = self.charm.unit.get_container(self.container_name)
            _run_update_ca_certificates(container)

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service, restarting only if config changed.

        configure_container() / write_config() have already been called by
        configure_containers(); skip them here to avoid duplicate pebble
        pull() RPCs.  The a2ensite exec and start_wsgi calls are kept.
        """
        restart = bool(self._files_changed)
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(
                ["a2ensite", self.wsgi_service_name], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2ensite warn: %s", line.strip())
            logger.debug("Output from a2ensite: \n%s", out)
        except ops.pebble.ExecError:
            logger.exception(
                "Failed to enable %s site in apache", self.wsgi_service_name
            )
        self.files_changed(self._files_changed)
        self.start_wsgi(restart=restart)
        self.status.set(ops.ActiveStatus(""))


@sunbeam_tracing.trace_type
class OctaviaControllerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for octavia-controller container.

    Runs all four controller services in a single container:
      - octavia-driver-agent   (always on)
      - octavia-housekeeping   (always on)
      - octavia-health-manager (Amphora only, startup: disabled)
      - octavia-worker         (Amphora only, startup: disabled)

    The Amphora services are started/stopped by
    ``_reconcile_amphora_containers`` based on the
    ``amphora-network-attachment`` config option.
    """

    def configure_container(
        self, context: sunbeam_core.OPSCharmContexts
    ) -> None:
        """Write configuration files and update CA certificates."""
        super().configure_container(context)
        if self.pebble_ready:
            container = self.charm.unit.get_container(self.container_name)
            _run_update_ca_certificates(container)

    def get_layer(self) -> dict:
        """Octavia controller services layer.

        :returns: pebble layer configuration for all controller services
        :rtype: dict
        """
        return {
            "summary": "octavia controller layer",
            "description": "pebble configuration for octavia controller services",
            "services": {
                "octavia-driver-agent": {
                    "override": "replace",
                    "summary": "Octavia Driver Agent",
                    "command": "octavia-driver-agent",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                },
                "octavia-housekeeping": {
                    "override": "replace",
                    "summary": "Octavia Housekeeping",
                    "command": "octavia-housekeeping",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                },
                "octavia-health-manager": {
                    "override": "replace",
                    "summary": "Octavia Health Manager",
                    "command": "octavia-health-manager",
                    # startup: disabled — only started by
                    # _reconcile_amphora_containers when
                    # amphora-network-attachment is configured.
                    "startup": "disabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                },
                "octavia-worker": {
                    "override": "replace",
                    "summary": "Octavia Worker",
                    "command": "octavia-worker",
                    # startup: disabled — see octavia-health-manager comment.
                    "startup": "disabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                },
            },
        }

    @property
    def service_ready(self) -> bool:
        """Service is ready when the always-on services are running.

        health-manager and worker are amphora-conditional; their absence
        does not block readiness when amphora-network-attachment is unset.
        """
        if not self.pebble_ready:
            return False
        container = self.charm.unit.get_container(self.container_name)
        services = container.get_services()
        # Guard against checking before the layer has been added (services
        # will be empty if add_layer has not yet been called).
        if not services:
            return False
        return all(
            name in services and services[name].is_running()
            for name in _CONTROLLER_ALWAYS_ON_SERVICES
        )

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Start always-on services, restarting them only if config changed.

        configure_container() has already been called by configure_containers()
        before init_container_services() is invoked, so we skip the duplicate
        call here to avoid a second round of pebble pull() RPCs per hook.

        health-manager and worker have startup: disabled and are only
        started/stopped by _reconcile_amphora_containers when
        amphora-network-attachment is configured.  However, if they are
        already running and octavia.conf has changed (e.g. amp-flavor-id was
        updated) they must be restarted here so they pick up the new config —
        _reconcile_amphora_containers only starts/stops but never restarts.
        """
        restart = bool(self._files_changed)
        container = self.charm.unit.get_container(self.container_name)
        # Add the layer so pebble knows about all four services.  Using
        # combine=True makes this idempotent when _on_service_pebble_ready
        # has already added it.
        container.add_layer(self.service_name, self.get_layer(), combine=True)
        for svc_name in _CONTROLLER_ALWAYS_ON_SERVICES:
            svcs = container.get_services(svc_name)
            svc = svcs.get(svc_name)
            if svc is None:
                continue
            if not svc.is_running():
                container.start(svc_name)
            elif restart:
                container.restart(svc_name)
        if restart:
            # Restart any Amphora services that are already running so they
            # pick up the updated octavia.conf.  Services that are not yet
            # running are left alone — _reconcile_amphora_containers will
            # start them once all prerequisites are met.
            svcs = container.get_services(*_CONTROLLER_AMPHORA_SERVICES)
            for svc_name, svc in svcs.items():
                if svc.is_running():
                    container.restart(svc_name)
        self._reset_files_changed()
        self.status.set(ops.ActiveStatus(""))

    def start_amphora_services(self) -> None:
        """Start health-manager and worker services."""
        if not self.pebble_ready:
            return
        container = self.charm.unit.get_container(self.container_name)
        for svc_name in _CONTROLLER_AMPHORA_SERVICES:
            svcs = container.get_services(svc_name)
            svc = svcs.get(svc_name)
            if svc and not svc.is_running():
                container.start(svc_name)

    def stop_amphora_services(self) -> None:
        """Stop health-manager and worker services."""
        if not self.pebble_ready:
            return
        container = self.charm.unit.get_container(self.container_name)
        svcs = container.get_services(*_CONTROLLER_AMPHORA_SERVICES)
        running = [name for name, svc in svcs.items() if svc.is_running()]
        if running:
            container.stop(*running)


@sunbeam_tracing.trace_type
class OVNContext(sunbeam_config_contexts.ConfigContext):
    """OVN configuration."""

    def context(self) -> dict:
        """Configuration context."""
        return {
            "ovn_key": "/etc/octavia/ovn_private_key.pem",
            "ovn_cert": "/etc/octavia/ovn_certificate.pem",
            "ovn_ca_cert": "/etc/octavia/ovn_ca_cert.pem",
        }


@sunbeam_tracing.trace_type
class AmphoraHealthManagerContext(sunbeam_config_contexts.ConfigContext):
    """Amphora Health Manager configuration."""

    def context(self) -> dict:
        """Configuration context for Amphora health manager."""
        ctxt = {}

        try:
            peers_rel = self.charm.peers.interface.peers_rel
        except AttributeError:
            peers_rel = None

        if peers_rel:
            local_ip = peers_rel.data[self.charm.model.unit].get("lbmgmt-ip")
            if local_ip:
                ctxt["bind_ip"] = local_ip

            all_ips = self.charm.peers.get_all_unit_values(
                "lbmgmt-ip", include_local_unit=True
            )
            if all_ips:
                ctxt["controller_ip_port_list"] = ",".join(
                    [f"{ip}:{OCTAVIA_HEALTH_MANAGER_PORT}" for ip in all_ips]
                )

        if hasattr(self.charm, "peers"):
            heartbeat_key = self.charm.get_heartbeat_key()
            if heartbeat_key:
                ctxt["heartbeat_key"] = heartbeat_key

        return ctxt


@sunbeam_tracing.trace_type
class AmphoraTlsCertificatesHandler(sunbeam_rhandlers.TlsCertificatesHandler):
    """TLS certificates handler for Amphora-specific relations.

    Extends TlsCertificatesHandler to support arbitrary relation names
    instead of the default 'certificates' relation that the base class
    hardcodes in setup_event_handler.
    """

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers using self.relation_name."""
        logger.debug(
            "Setting up certificates event handler for %s", self.relation_name
        )
        mode = Mode.APP if self.app_managed_certificates else Mode.UNIT
        self.certificates = sunbeam_tracing.trace_type(
            TLSCertificatesRequiresV4
        )(self.charm, self.relation_name, self.certificate_requests, mode)

        self.framework.observe(
            self.certificates.on.certificate_available,
            self._on_certificate_available,
        )
        return self.certificates

    def update_relation_data(self) -> None:
        """No-op: skip cert.sync() to prevent re-entrancy into configure_charm.

        The base class calls self.certificates.sync() here, which synchronously
        emits certificate_available if certs are already present. That event
        re-enters configure_charm() while the outer call is still inside
        update_relations(), causing a full duplicate configure_unit() run
        (including ~50 pebble pull() RPCs) before the outer run even starts.
        Cert data is read directly from the relation in context() on every hook,
        so skipping sync() here loses nothing.
        """


@sunbeam_tracing.trace_type
class AmphoraCertificatesContext(sunbeam_config_contexts.ConfigContext):
    """Amphora Certificates configuration context.

    Reads certificate material from the amphora-issuing-ca and
    amphora-controller-cert tls-certificates relations.  The context
    keys are intentionally identical to the old config-option-based keys
    so that all Jinja2 templates (issuing_ca.pem.j2, controller_cert.pem.j2,
    etc.) work without modification.
    """

    def context(self) -> dict:
        """Configuration context for Amphora certificates."""
        if self.charm.unit.is_leader():
            return self._leader_context()
        return self._non_leader_context()

    def _leader_context(self) -> dict:
        """Build the cert context on the leader unit.

        Reads directly from the TLS certificate relation handlers.
        The tls_certificates_interface library allows only the leader to
        retrieve CSRs (and thus assigned certs) when mode is Mode.APP.
        """
        ctxt = {}
        issuing_handler = self.charm.amphora_issuing_ca
        if issuing_handler.ready:
            for _cn, cert in issuing_handler.get_certs():
                if cert is not None:
                    ctxt["lb_mgmt_issuing_cacert"] = str(cert.certificate)
                    ctxt["lb_mgmt_issuing_ca_private_key"] = (
                        issuing_handler.get_private_key() or ""
                    )
                    # cert.ca is the signing CA that issued the issuing CA
                    # certificate (may be an intermediate or root CA).
                    # Appended to issuing_ca.pem so OpenSSL can verify the
                    # full chain when validating per-Amphora certs.
                    if cert.ca:
                        ctxt["lb_mgmt_issuing_ca_root"] = str(cert.ca)
                    break
        # Controller cert + CA from the amphora-controller-cert relation.
        # The controller cert file is a PEM bundle: cert || private-key,
        # matching the format expected by Octavia's haproxy_amphora section.
        controller_handler = self.charm.amphora_controller_cert
        if controller_handler.ready:
            for _cn, cert in controller_handler.get_certs():
                if cert is not None:
                    ctxt["lb_mgmt_controller_cacert"] = str(cert.ca)
                    private_key = controller_handler.get_private_key() or ""
                    ctxt["lb_mgmt_controller_cert"] = (
                        str(cert.certificate).rstrip("\n") + "\n" + private_key
                    )
                    break
        return ctxt

    def _non_leader_context(self) -> dict:
        """Build the cert context on non-leader units.

        Non-leader units cannot read from tls-certificates relations directly
        when mode is Mode.APP (the library skips non-leader units).  Instead
        they read cert material from the peer app databag that the leader
        populates in _sync_amphora_certs_to_peer_databag().
        """
        ctxt = {}
        try:
            peer_data = {
                "issuing_cacert": self.charm.peers.get_app_data(
                    AMPHORA_ISSUING_CACERT_PEER_KEY
                ),
                "issuing_ca_root": self.charm.peers.get_app_data(
                    AMPHORA_ISSUING_CA_ROOT_PEER_KEY
                ),
                "controller_cacert": self.charm.peers.get_app_data(
                    AMPHORA_CONTROLLER_CACERT_PEER_KEY
                ),
                "controller_cert_pem": self.charm.peers.get_app_data(
                    AMPHORA_CONTROLLER_CERT_PEER_KEY
                ),
                "secret_id": self.charm.peers.get_app_data(
                    AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY
                ),
            }
        except Exception as e:
            logger.debug(
                "Could not read Amphora cert data from peer databag: %s", e
            )
            return ctxt
        if not all(
            [
                peer_data["issuing_cacert"],
                peer_data["controller_cacert"],
                peer_data["controller_cert_pem"],
                peer_data["secret_id"],
            ]
        ):
            logger.debug(
                "Amphora cert data not yet available in peer app databag; "
                "waiting for leader to populate it"
            )
            return ctxt
        try:
            secret = self.charm.model.get_secret(id=peer_data["secret_id"])
            pk_content = secret.get_content(refresh=True)
        except ModelError as e:
            logger.error(
                "Failed to read Amphora cert private-keys secret: %s", e
            )
            return ctxt
        ctxt["lb_mgmt_issuing_cacert"] = peer_data["issuing_cacert"]
        ctxt["lb_mgmt_issuing_ca_private_key"] = pk_content.get(
            "issuing-ca-private-key", ""
        )
        if peer_data["issuing_ca_root"]:
            ctxt["lb_mgmt_issuing_ca_root"] = peer_data["issuing_ca_root"]
        ctxt["lb_mgmt_controller_cacert"] = peer_data["controller_cacert"]
        ctxt["lb_mgmt_controller_cert"] = (
            peer_data["controller_cert_pem"].rstrip("\n")
            + "\n"
            + pk_content.get("controller-cert-private-key", "")
        )
        return ctxt


@sunbeam_tracing.trace_type
class KubernetesResourcePatcher(Object):
    """Generic Kubernetes resource patcher for StatefulSets.

    Patches StatefulSet resources using strategic merge patch. Can be used to
    update pod template annotations, resource limits, or other spec fields.
    Inspired by prometheus-k8s-operator's ResourcePatcher.
    """

    def __init__(
        self,
        charm: sunbeam_charm.OSBaseOperatorCharmK8S,
        statefulset_name: str,
        refresh_event: List[BoundEvent] | None = None,
    ):
        """Initialize the resource patcher.

        :param charm: The charm instance
        :param statefulset_name: Name of the StatefulSet to patch
        :param refresh_event: Optional list of events that trigger reconciliation
        """
        super().__init__(charm, "kubernetes-resource-patcher")
        self.charm = charm
        self._statefulset_name = statefulset_name

        self._lightkube_client = None
        self._lightkube_field_manager: str = self.charm.app.name

        # Observe events for reconciliation
        if refresh_event:
            if not isinstance(refresh_event, list):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._patch_statefulset)

    @property
    def lightkube_client(self):
        """Returns a lightkube client configured for this charm."""
        if self._lightkube_client is None:
            self._lightkube_client = Client(
                namespace=self.charm.model.name,
                field_manager=self._lightkube_field_manager,
            )
        return self._lightkube_client

    def get_patch(self) -> dict:
        """Get the patch to apply to the StatefulSet.

        This method should be overridden by subclasses to provide
        specific patch content.

        :returns: The patch dictionary to apply
        :rtype: dict
        """
        return {}

    def is_patched(self, statefulset: StatefulSet) -> bool:
        """Check if the StatefulSet already has the desired patch.

        This method should be overridden by subclasses to provide
        specific comparison logic.

        :param statefulset: The StatefulSet to check
        :returns: True if already patched, False otherwise
        :rtype: bool
        """
        return False

    def _patch_statefulset(self, _) -> None:
        """Patch the StatefulSet resource."""
        if not self.charm.unit.is_leader():
            return

        patch = self.get_patch()
        if not patch:
            logger.debug(
                f"No patch to apply for StatefulSet {self._statefulset_name}"
            )
            return

        try:
            statefulset = self.lightkube_client.get(
                StatefulSet,
                name=self._statefulset_name,
                namespace=self.charm.model.name,
            )

            if self.is_patched(statefulset):
                logger.debug(
                    f"StatefulSet {self._statefulset_name} is already patched"
                )
                return

            self.lightkube_client.patch(
                StatefulSet,
                name=self._statefulset_name,
                obj=patch,
                patch_type=PatchType.STRATEGIC,
                namespace=self.charm.model.name,
            )
            logger.info(f"Patched StatefulSet {self._statefulset_name}")

        except ApiError as e:
            logger.error(
                f"Failed to patch StatefulSet {self._statefulset_name}: {e}"
            )

    def patch_now(self) -> None:
        """Manually trigger a patch of the resource."""
        self._patch_statefulset(None)


@sunbeam_tracing.trace_type
class KubernetesPodAnnotationPatcher(KubernetesResourcePatcher):
    """Patch pod template annotations for StatefulSets.

    Adds or updates annotations in the pod template of a StatefulSet.
    Useful for network configuration (multus, Whereabouts) or other
    pod-level metadata.
    """

    def __init__(
        self,
        charm: sunbeam_charm.OSBaseOperatorCharmK8S,
        statefulset_name: str,
        annotations: dict[str, str],
        refresh_event: List[BoundEvent] | None = None,
    ):
        """Initialize the pod annotation patcher.

        :param charm: The charm instance
        :param statefulset_name: Name of the StatefulSet to patch
        :param annotations: Dictionary of annotations to add to pod template
        :param refresh_event: Optional list of events that trigger reconciliation
        """
        super().__init__(charm, statefulset_name, refresh_event)
        self._annotations = annotations

    def get_patch(self) -> dict:
        """Get the annotation patch for the StatefulSet.

        :returns: Patch containing pod template annotations
        :rtype: dict
        """
        if not self._annotations:
            return {}

        return {
            "spec": {
                "template": {"metadata": {"annotations": self._annotations}}
            }
        }

    def is_patched(self, statefulset: StatefulSet) -> bool:
        """Check if annotations are already applied.

        :param statefulset: The StatefulSet to check
        :returns: True if all annotations are already present with correct values
        :rtype: bool
        """
        if not self._annotations:
            return True

        # Get current annotations
        current_annotations = {}
        if (
            hasattr(statefulset.spec.template.metadata, "annotations")
            and statefulset.spec.template.metadata.annotations
        ):
            current_annotations = (
                statefulset.spec.template.metadata.annotations
            )

        # Check if all desired annotations are present with correct values
        for key, value in self._annotations.items():
            if current_annotations.get(key) != value:
                return False

        return True


@sunbeam_tracing.trace_type
class OctaviaNetworkAnnotationPatcher(KubernetesPodAnnotationPatcher):
    """Patch Octavia pod annotations for amphora management network.

    Dynamically adds network annotations based on charm configuration.
    Used to attach additional network interfaces to Octavia pods for
    amphora health manager communication.
    """

    # DaemonSets in kube-system that must be running before the Multus
    # network annotation is applied to the StatefulSet pod template.
    _CNI_DAEMONSETS = (
        "kube-multus-ds",
        "openstack-port-daemon",
        "ovs-cni-amd64",
    )

    def __init__(
        self,
        charm: sunbeam_charm.OSBaseOperatorCharmK8S,
        statefulset_name: str,
        refresh_event: List[BoundEvent] | None = None,
    ):
        """Initialize the Octavia network annotation patcher.

        :param charm: The charm instance
        :param statefulset_name: Name of the StatefulSet to patch
        :param refresh_event: Optional list of events that trigger reconciliation
        """
        super().__init__(charm, statefulset_name, {}, refresh_event)

    def cni_ready(self) -> tuple[bool, str]:
        """Check whether the required CNI DaemonSets are running in kube-system.

        Verifies that kube-multus-ds, openstack-port-daemon and ovs-cni-amd64
        all exist and have at least one ready pod scheduled before attempting
        to add the Multus network annotation to the pod template.

        :returns: ``(True, "")`` when all DaemonSets are ready, or
                  ``(False, reason)`` describing the first unmet condition.
        :rtype: tuple[bool, str]
        """
        for ds_name in self._CNI_DAEMONSETS:
            try:
                ds = self.lightkube_client.get(
                    DaemonSet, name=ds_name, namespace="kube-system"
                )
            except ApiError:
                return False, f"{ds_name} DaemonSet not found in kube-system"
            status = ds.status
            if not status:
                return False, f"{ds_name} has no status yet"
            desired = status.desiredNumberScheduled or 0
            ready = status.numberReady or 0
            if desired == 0 or ready < desired:
                return False, f"{ds_name} not ready ({ready}/{desired} pods)"
        return True, ""

    def _patch_statefulset(self, event) -> None:
        """Patch the StatefulSet only when CNI infrastructure is ready.

        Defers the event when ``amphora-network-attachment`` is configured but
        the required CNI DaemonSets are not yet fully running.  Deferring
        re-queues this event so it is retried before the next hook handler
        runs (e.g. peers-relation-changed fires seconds after config-changed),
        without relying on update-status for recovery.
        """
        if self.charm.config.get("amphora-network-attachment"):
            ready, reason = self.cni_ready()
            if not ready:
                logger.warning(
                    "Waiting for CNI infrastructure: %s — deferring event",
                    reason,
                )
                event.defer()
                return
        super()._patch_statefulset(event)

    def get_patch(self) -> dict:
        """Get network annotations from current charm config.

        When ``amphora-network-attachment`` is set, adds the Multus CNI
        annotation to the pod template.  When it is unset, returns a patch
        that explicitly nulls out the annotation so any previously-set value
        is removed from the StatefulSet (strategic-merge null = delete).

        :returns: Patch containing pod template annotations for network config
        :rtype: dict
        """
        network_config = self.charm.config.get("amphora-network-attachment")
        annotation_key = "k8s.v1.cni.cncf.io/networks"

        # Always return a patch so _patch_statefulset never short-circuits on
        # an empty dict.  When unsetting we need the null patch to remove it.
        annotation_value = network_config if network_config else None
        return {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {annotation_key: annotation_value}
                    }
                }
            }
        }

    def is_patched(self, statefulset: StatefulSet) -> bool:
        """Check if the annotation already matches the desired state.

        :param statefulset: The StatefulSet to check
        :returns: True if the annotation already matches the desired state
        :rtype: bool
        """
        network_config = self.charm.config.get("amphora-network-attachment")
        annotation_key = "k8s.v1.cni.cncf.io/networks"

        current_annotations = {}
        if (
            hasattr(statefulset.spec.template.metadata, "annotations")
            and statefulset.spec.template.metadata.annotations
        ):
            current_annotations = (
                statefulset.spec.template.metadata.annotations
            )

        if not network_config:
            # Desired state: annotation absent.
            return annotation_key not in current_annotations

        return current_annotations.get(annotation_key) == network_config


@sunbeam_tracing.trace_sunbeam_charm
class OctaviaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    service_name = "octavia-api"
    wsgi_admin_script = "/usr/bin/octavia-wsgi"
    wsgi_public_script = "/usr/bin/octavia-wsgi"

    db_sync_cmds = [
        [
            "octavia-db-manage",
            "--config-file",
            "/etc/octavia/octavia.conf",
            "upgrade",
            "head",
        ]
    ]

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(
            self.on.peers_relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.on.peers_relation_changed, self._on_peer_relation_changed
        )
        self.framework.observe(
            self.on.peers_relation_departed, self._on_peer_relation_departed
        )

        # Dedicated status slot for amphora management network readiness.
        # Priority 5 is intentionally the second-lowest in the pool so that
        # all core service slots (workload=100, config=95, bootstrap=90) WIN
        # in any tie; Amphora concerns are surfaced only when everything else
        # is fine.
        self.amphora_net_status = compound_status.Status(
            "amphora-network", priority=5
        )
        self.status_pool.add(self.amphora_net_status)

        # Status slot for Amphora readiness (cert relations, barbican).
        # Priority 3 is the lowest in the pool — it yields to ALL other slots
        # (including amphora_net_status at 5) in any tie.  The early-return
        # guard in post_config_setup() still handles Blocked-vs-Waiting across
        # status types.
        self.amphora_status = compound_status.Status("amphora", priority=3)
        self.status_pool.add(self.amphora_status)

        # Status slot for configuration validation errors.
        # Priority stack (highest wins): workload=100, config_status=95,
        # bootstrap=90, amphora_net_status=5, amphora_status=3.
        self.config_status = compound_status.Status("config", priority=95)
        self.status_pool.add(self.config_status)

        # Setup network annotation patcher for amphora management network
        self.network_patcher = OctaviaNetworkAnnotationPatcher(
            charm=self,
            statefulset_name=self.app.name,
            refresh_event=[self.on.config_changed, self.on.upgrade_charm],
        )

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/octavia/octavia.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "octavia"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "octavia"

    @property
    def service_endpoints(self):
        """Return service endpoints for the service."""
        return [
            {
                "service_name": "octavia",
                "type": "load-balancer",
                "description": "OpenStack Octavia API",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 9876

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        return [
            OctaviaWSGIPebbleHandler(
                self,
                OCTAVIA_API_CONTAINER,
                self.service_name,
                self._api_container_configs(),
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            OctaviaControllerPebbleHandler(
                self,
                OCTAVIA_CONTROLLER_CONTAINER,
                "octavia-controller",
                self._controller_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
        ]

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("identity-ops", handlers):
            self.id_ops = sunbeam_rhandlers.IdentityResourceRequiresHandler(
                self,
                "identity-ops",
                self.handle_keystone_ops,
                mandatory="identity-ops" in self.mandatory_relations,
            )
            handlers.append(self.id_ops)
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_rhandlers.OVSDBCMSRequiresHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                external_connectivity=self.remote_external_access,
                mandatory="ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        if self.can_add_handler("barbican-service", handlers):
            self.barbican_svc = (
                sunbeam_rhandlers.ServiceReadinessRequiresHandler(
                    self,
                    "barbican-service",
                    self.configure_charm,
                    "barbican-service" in self.mandatory_relations,
                )
            )
            handlers.append(self.barbican_svc)
        self.amphora_issuing_ca = AmphoraTlsCertificatesHandler(
            self,
            "amphora-issuing-ca",
            self.configure_charm,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name="octavia-amphora-issuing-ca",
                    is_ca=True,
                )
            ],
            # The issuing CA cert is shared across all units — one CSR per app.
            app_managed_certificates=True,
            mandatory=False,
        )
        handlers.append(self.amphora_issuing_ca)
        self.amphora_controller_cert = AmphoraTlsCertificatesHandler(
            self,
            "amphora-controller-cert",
            self.configure_charm,
            certificate_requests=[
                CertificateRequestAttributes(
                    common_name=f"{self.app.name}-controller",
                )
            ],
            # The controller cert is shared across all units — one CSR per app.
            app_managed_certificates=True,
            mandatory=False,
        )
        handlers.append(self.amphora_controller_cert)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def _common_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Config files shared by all Octavia containers."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/octavia.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                self.service_group,
                0o640,
            ),
        ]

    def _amphora_cert_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Amphora mTLS cert files used by worker, housekeeping and health-manager."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/certs/issuing_ca.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/certs/issuing_ca_key.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/certs/controller_cert.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
        ]

    def _ovn_cert_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """OVN TLS cert files used only by octavia-driver-agent."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/ovn_private_key.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/ovn_certificate.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/ovn_ca_cert.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
        ]

    def _api_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Config files for octavia-api (WSGI) container.

        octavia-api loads the OVN provider driver on startup to validate
        provider names in API requests, so it needs the OVN TLS certs even
        though it does not run the driver-agent service.
        """
        return (
            self._common_container_configs()
            + self._ovn_cert_configs()
            + [
                sunbeam_core.ContainerConfigFile(
                    "/etc/octavia/api_audit_map.conf",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
            ]
        )

    def _controller_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Config files for octavia-controller container.

        The controller container runs driver-agent (needs OVN certs),
        housekeeping (needs Amphora certs), health-manager (needs
        controller_ca + issuing_ca + controller_cert), and worker (needs
        controller_ca + Amphora certs).  The union of all four sets is:
        common configs + OVN certs + controller_ca + Amphora certs.
        """
        return (
            self._common_container_configs()
            + self._ovn_cert_configs()
            + [
                sunbeam_core.ContainerConfigFile(
                    "/etc/octavia/certs/controller_ca.pem",
                    self.service_user,
                    self.service_group,
                    0o640,
                ),
            ]
            + self._amphora_cert_configs()
        )

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Not used directly — each handler uses its own per-container config list."""
        return self._common_container_configs()

    def handle_keystone_ops(self, event: ops.EventBase) -> None:
        """Event handler for identity ops."""
        if isinstance(event, identity_resource.IdentityOpsProviderReadyEvent):
            self._state.identity_ops_ready = True

            if not self.unit.is_leader():
                return

            # Send op request only by leader unit
            ops = self._get_octavia_role_ops()
            id_ = self.hash_ops(ops)
            request = {
                "id": id_,
                "tag": "octavia_roles_setup",
                "ops": ops,
            }
            logger.debug("Sending ops request: %r", request)
            self.id_ops.interface.request_ops(request)
        elif isinstance(
            event,
            identity_resource.IdentityOpsProviderGoneAwayEvent,
        ):
            self._state.identity_ops_ready = False
        elif isinstance(event, identity_resource.IdentityOpsResponseEvent):
            if not self.unit.is_leader():
                return
            response = self.id_ops.interface.response
            logger.debug("Got response from keystone: %r", response)
            request_tag = response.get("tag")
            if request_tag == "octavia_roles_setup":
                self._handle_octavia_roles_setup(event)

    def _handle_octavia_roles_setup(
        self,
        event: ops.EventBase,
    ) -> None:
        """Handle roles setup response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get(
                "ops",
                [],
            )
        } != {0}:
            logger.error("Failed to setup octavia roles")

    def hash_ops(self, ops: list) -> str:
        """Return the sha1 of the requested ops."""
        return hashlib.sha1(json.dumps(ops).encode()).hexdigest()

    def _get_octavia_role_ops(self) -> list:
        """Generate ops request for creation of roles."""
        roles = [
            "load-balancer_observer",
            "load-balancer_global_observer",
            "load-balancer_member",
            "load-balancer_quota_admin",
            "load-balancer_admin",
        ]
        ops = [
            {"name": "create_role", "params": {"name": name}} for name in roles
        ]
        return ops

    def _on_upgrade_charm(self, event: ops.framework.EventBase):
        """Handle the upgrade charm event."""
        logger.info("Handling upgrade-charm event")
        self.certs.validate_and_regenerate_certificates_if_needed()
        # ops-sunbeam doesn't wire upgrade_charm -> configure_charm by default.
        # configure_charm -> configure_unit -> _set_lbmgmt_ip re-detects the IP.
        self.configure_charm(event)

    def _read_pod_network_status(self, pod_name: str) -> list | None:
        """Fetch and parse k8s.v1.cni.cncf.io/network-status from the pod.

        :returns: parsed list of network-status entries, or None on error
        """
        namespace = self.model.name
        try:
            pod = self.network_patcher.lightkube_client.get(
                Pod, name=pod_name, namespace=namespace
            )
        except Exception as e:
            logger.warning(f"Could not fetch pod {pod_name}: {e}")
            return None

        if not (
            hasattr(pod, "metadata")
            and pod.metadata
            and pod.metadata.annotations
        ):
            logger.warning(
                "Pod has no annotations; network-status not yet set"
            )
            return None

        raw = pod.metadata.annotations.get("k8s.v1.cni.cncf.io/network-status")
        if not raw:
            logger.warning(
                "k8s.v1.cni.cncf.io/network-status annotation not present"
            )
            return None

        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Failed to parse network-status annotation: {e}")
            return None

    def _ip_from_network_status_entries(
        self, entries: list, nad_name: str
    ) -> str | None:
        """Find the first IP for *nad_name* in network-status entries.

        The ``name`` field in each entry is either ``<nad-name>`` or
        ``<namespace>/<nad-name>``.  When the operator omits the namespace
        prefix in the ``amphora-network-attachment`` config option, Multus
        resolves the NAD from the pod's own namespace and the annotation will
        contain the fully-qualified ``<namespace>/<nad-name>`` form.  Both
        forms are matched here.

        :returns: IP address string, or None if not found
        """
        for entry in entries:
            entry_name = entry.get("name", "")
            if entry_name == nad_name or entry_name.endswith(f"/{nad_name}"):
                ips = entry.get("ips", [])
                if ips:
                    logger.info(
                        f"Found lb-mgmt IP {ips[0]} from network-status "
                        f"entry {entry_name!r}"
                    )
                    return ips[0]
                logger.warning(
                    f"Network-status entry {entry_name!r} has no IPs"
                )
                return None
        logger.warning(
            f"NAD {nad_name!r} not found in network-status annotation"
        )
        return None

    def _get_lbmgmt_ip_from_network_status(self) -> str | None:
        """Get the lb-mgmt IP from the pod's network-status annotation.

        Reads ``k8s.v1.cni.cncf.io/network-status`` from the current pod's
        metadata and returns the first IP of the entry whose ``name`` matches
        the ``amphora-network-attachment`` config value (NAD name).

        The annotation contains a JSON list of network attachment entries, e.g.::

            [{"name": "cilium", "interface": "eth0", ...},
             {"name": "<namespace>/<nad-name>", "interface": "net1",
              "ips": ["192.170.0.1"], ...}]

        :returns: IP address string or None if not found
        :rtype: str | None
        """
        nad_name = self.config.get("amphora-network-attachment", "")
        if not nad_name:
            return None

        # Derive pod name from unit name (e.g. "octavia/0" -> "octavia-0").
        # HOSTNAME is not reliably set in Juju charm hook processes.
        pod_name = self.unit.name.replace("/", "-")
        if not pod_name:
            logger.warning(
                "Could not determine pod name from unit name; "
                "cannot read pod annotations"
            )
            return None

        entries = self._read_pod_network_status(pod_name)
        if entries is None:
            return None

        return self._ip_from_network_status_entries(entries, nad_name)

    def _set_lbmgmt_ip(self) -> None:
        """Set lbmgmt-ip in peer unit data.

        Detects second interface IP and sets it in unit data for
        Amphora-based load balancer support.  Updates the
        ``amphora-network`` status slot so operators can see whether
        the management interface is ready.
        """
        network_attachment = self.config.get("amphora-network-attachment", "")
        if not network_attachment:
            # No network attachment configured — nothing expected, no issue.
            self.amphora_net_status.set(ops.ActiveStatus(""))
            return
        # Check CNI infrastructure before inspecting the pod's network-status.
        # If Multus and the required CNI DaemonSets are not yet running, the
        # pod will never get a 2nd NIC even if the annotation is present.
        cni_ready, cni_reason = self.network_patcher.cni_ready()
        if not cni_ready:
            msg = f"Waiting for CNI infrastructure: {cni_reason}"
            logger.warning(msg)
            self.amphora_net_status.set(ops.WaitingStatus(msg))
            return
        lbmgmt_ip = self._get_lbmgmt_ip_from_network_status()
        if lbmgmt_ip:
            # Read existing value via the peers relation handler, consistent
            # with AmphoraHealthManagerContext and other peers data access.
            try:
                peers_rel = self.peers.interface.peers_rel
            except AttributeError:
                peers_rel = None
            current_ip = (
                peers_rel.data[self.model.unit].get("lbmgmt-ip")
                if peers_rel
                else None
            )
            if lbmgmt_ip != current_ip:
                self.peers.set_unit_data({"lbmgmt-ip": lbmgmt_ip})
                logger.info(f"Set lbmgmt-ip to {lbmgmt_ip}")
            else:
                logger.debug(
                    f"lbmgmt-ip already set to {lbmgmt_ip}, skipping write"
                )
            self.amphora_net_status.set(ops.ActiveStatus(""))
        elif network_attachment:
            # The operator has configured a network attachment but the
            # interface is not yet available (pod may still be rolling).
            msg = "Amphora management network interface not detected"
            logger.warning(msg)
            self.amphora_net_status.set(ops.WaitingStatus(msg))

    def _on_peer_relation_created(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle peer relation created event.

        configure_charm -> configure_unit -> _set_lbmgmt_ip detects the
        management interface and writes bind_ip into peer unit data.
        """
        logger.info("Setting peer unit data for Octavia Amphora support")
        self.configure_charm(event)

    def _on_peer_relation_changed(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle peer relation changed event.

        When other units join and update their lbmgmt-ip, this event fires
        to trigger reconfiguration so controller_ip_port_list gets updated.
        configure_charm -> configure_unit -> _set_lbmgmt_ip handles the IP
        detection; calling it here directly would be a redundant second call.
        """
        logger.info(
            "Peer relation changed, reconfiguring for updated controller IPs"
        )
        self.configure_charm(event)

    def _on_peer_relation_departed(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle peer relation departed event.

        When units are removed, this event fires on remaining units to
        trigger reconfiguration and remove the departed unit's IP from
        controller_ip_port_list.
        """
        logger.info(
            "Peer departed, reconfiguring to remove departed unit from controller IPs"
        )
        self.configure_charm(event)

    def generate_heartbeat_key(self) -> None:
        """Generate and store the Amphora heartbeat key as a Juju secret.

        The key secures health manager <-> Amphora heartbeat UDP packets.
        Only the secret ID is stored in peer app data; all units retrieve
        the value via the Juju secret API.
        """
        try:
            secret_id = self.peers.get_app_data(OCTAVIA_HEARTBEAT_KEY)
            if secret_id:
                logger.debug("Heartbeat key secret already exists")
                return

            key = secrets.token_hex(32)
            heartbeat_secret = self.model.app.add_secret(
                {OCTAVIA_HEARTBEAT_KEY: key},
                label=OCTAVIA_HEARTBEAT_KEY,
            )
            self.peers.set_app_data(
                {OCTAVIA_HEARTBEAT_KEY: heartbeat_secret.id}
            )
            logger.info(
                "Generated and stored Amphora heartbeat key as Juju secret"
            )
        except ModelError as e:
            logger.error(f"Failed to create heartbeat key secret: {e}")

    def get_heartbeat_key(self) -> str | None:
        """Retrieve the Amphora heartbeat key from Juju secret.

        :returns: The heartbeat key value or None if not found
        :rtype: str | None
        """
        secret_id = self.peers.get_app_data(OCTAVIA_HEARTBEAT_KEY)
        if secret_id:
            try:
                secret = self.model.get_secret(id=secret_id)
                return secret.get_content(refresh=True).get(
                    OCTAVIA_HEARTBEAT_KEY
                )
            except ModelError as e:
                logger.error(f"Failed to retrieve heartbeat key secret: {e}")
                return None
        return None

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(OVNContext(self, "ovn"))
        contexts.append(AmphoraCertificatesContext(self, "amphora_certs"))
        contexts.append(AmphoraHealthManagerContext(self, "amphora_health"))
        return contexts

    def _get_config_errors(self) -> list[str]:
        """Return a list of configuration validation errors.

        :returns: List of error messages; empty when config is valid.
        :rtype: list[str]
        """
        errors = []
        topology = self.config.get("loadbalancer-topology", "SINGLE")
        if topology not in _VALID_TOPOLOGY:
            errors.append(
                f"loadbalancer-topology {topology!r} must be one of: "
                + ", ".join(sorted(_VALID_TOPOLOGY))
            )
        anti_affinity = self.config.get("anti-affinity-policy", "auto")
        if anti_affinity not in _VALID_ANTI_AFFINITY:
            errors.append(
                f"anti-affinity-policy {anti_affinity!r} must be one of: "
                + ", ".join(sorted(_VALID_ANTI_AFFINITY))
            )
        log_protocol = self.config.get("log-protocol", "UDP")
        if log_protocol not in _VALID_LOG_PROTOCOL:
            errors.append(
                f"log-protocol {log_protocol!r} must be one of: "
                + ", ".join(sorted(_VALID_LOG_PROTOCOL))
            )
        return errors

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Catchall handler to configure charm services.

        Validates configuration before delegating to the base class.
        Only hard config-syntax errors (invalid topology/anti-affinity/
        log-protocol values) gate the call to super() — those can cause
        template rendering failures so there is nothing useful to do.

        Amphora readiness checks (barbican, cert relations) are surfaced in
        post_config_setup() so the unit always fully configures even when
        optional Amphora dependencies are not yet ready.
        """
        errors = self._get_config_errors()
        if errors:
            self.config_status.set(ops.BlockedStatus(errors[0]))
            return
        self.config_status.set(ops.ActiveStatus(""))
        super().configure_charm(event)

    def post_config_setup(self) -> None:
        """Surface Amphora readiness state after the unit is fully configured.

        Called by the base class after configure_unit() succeeds.  The unit
        is already running at this point; we just update amphora_status to
        reflect whether Amphora prerequisites are met.  Because
        STATUS_PRIORITIES["blocked"] < STATUS_PRIORITIES["active"], a
        Blocked amphora_status will dominate over an Active workload status
        in the compound-status pool without preventing the unit from
        configuring.
        """
        super().post_config_setup()
        if not self.config.get("amphora-network-attachment"):
            self.amphora_status.set(ops.ActiveStatus(""))
            return
        # If the amphora management network interface is not yet detected,
        # surface that error before reporting missing cert relations.
        if self.amphora_net_status.status.name != "active":
            self.amphora_status.set(ops.ActiveStatus(""))
            return
        if not self.model.relations.get(
            "amphora-issuing-ca"
        ) or not self.model.relations.get("amphora-controller-cert"):
            self.amphora_status.set(
                ops.BlockedStatus(
                    "amphora-issuing-ca and amphora-controller-cert "
                    "relations required for Amphora"
                )
            )
            return
        if not self.model.relations.get("barbican-service"):
            self.amphora_status.set(
                ops.BlockedStatus(
                    "barbican-service integration required for Amphora"
                )
            )
            return
        if not self.barbican_svc.ready:
            self.amphora_status.set(
                ops.WaitingStatus("Waiting for barbican-service")
            )
            return
        if not self.amphora_issuing_ca.ready:
            self.amphora_status.set(
                ops.BlockedStatus(
                    "Amphora issuing CA certificate not yet provided "
                    "by amphora-issuing-ca integration"
                )
            )
            return
        if not self.amphora_controller_cert.ready:
            self.amphora_status.set(
                ops.BlockedStatus(
                    "Amphora controller certificate not yet provided "
                    "by amphora-controller-cert integration"
                )
            )
            return
        self.amphora_status.set(ops.ActiveStatus(""))

    def _find_next_alembic_revision(
        self, history_out: str, current_rev: str
    ) -> str | None:
        """Find the next revision after current_rev in alembic history output.

        History output is newest-first with lines of the form:
            <prev_hash> -> <next_hash> [(head)], description

        Returns next_hash where prev_hash is a prefix/match for current_rev.
        When current_rev is empty (fresh DB), returns the base revision.
        """
        for line in history_out.splitlines():
            m = re.match(r"\s*([0-9a-f]+)\s*->\s*([0-9a-f]+)", line)
            if not m:
                continue
            prev, nxt = m.group(1), m.group(2)
            if current_rev and (
                current_rev.startswith(prev) or prev.startswith(current_rev)
            ):
                return nxt

        if not current_rev:
            # Fresh DB: the partial migration was the very first one.
            # History is newest-first; scan from the bottom for the base line
            # which has no preceding hash: " -> <rev>, description"
            for line in reversed(history_out.splitlines()):
                m = re.match(r"\s*->\s*([0-9a-f]+)", line)
                if m:
                    return m.group(1)
            # Fallback: take the last regular <prev> -> <next> entry.
            for line in reversed(history_out.splitlines()):
                m = re.match(r"\s*([0-9a-f]+)\s*->\s*([0-9a-f]+)", line)
                if m:
                    return m.group(2)

        return None

    def _stamp_partial_migration(
        self, container: ops.model.Container, db_sync_cmd: list
    ) -> None:
        """Identify and stamp the partially-applied alembic revision.

        Called when a 'Duplicate column name' error indicates that a previous
        db-sync timed out after the DDL auto-committed but before alembic
        wrote the new revision into alembic_version.
        """
        # Base prefix: everything before the alembic sub-command.
        # db_sync_cmd = ["octavia-db-manage", "--config-file", "<path>",
        #                "upgrade", "head"]
        base = db_sync_cmd[:3]

        # Discover the current recorded revision.
        current_rev = ""
        try:
            proc = container.exec(base + ["current"], timeout=60)
            out, _ = proc.wait_output()
            # `octavia-db-manage current` emits INFO/DEBUG log lines before
            # the revision hash.  The hash is always the last non-empty line.
            current_rev = ""
            for line in reversed(out.splitlines()):
                line = line.strip()
                if line:
                    current_rev = line.split()[0]
                    break
            logger.info("Current alembic revision: %r", current_rev)
        except Exception as e:
            logger.warning(
                "Could not determine current alembic revision: %s", e
            )

        # Fetch migration history.
        try:
            proc = container.exec(base + ["history"], timeout=60)
            history_out, _ = proc.wait_output()
        except Exception as e:
            logger.warning("Could not fetch alembic migration history: %s", e)
            return

        next_rev = self._find_next_alembic_revision(history_out, current_rev)
        if not next_rev:
            logger.warning(
                "Cannot determine next revision to stamp "
                "(current=%r); history:\n%s",
                current_rev,
                history_out,
            )
            return

        logger.warning(
            "Stamping alembic revision %s to recover partial migration "
            "(current=%r)",
            next_rev,
            current_rev,
        )
        try:
            proc = container.exec(base + ["stamp", next_rev], timeout=60)
            proc.wait_output()
            logger.info("Successfully stamped revision %s", next_rev)
        except Exception as e:
            logger.warning("Failed to stamp revision %s: %s", next_rev, e)

    def _exec_db_sync(
        self,
        container: ops.model.Container,
        cmd: list,
        timeout: float,
    ) -> None:
        """Execute a single db-sync command and log its output."""
        proc = container.exec(cmd, timeout=timeout)
        out, err = proc.wait_output()
        for line in (err or "").splitlines():
            logger.warning("db-sync stderr: %s", line.strip())
        for line in (out or "").splitlines():
            logger.info("db-sync stdout: %s", line.strip())

    def _is_duplicate_column_error(self, exc: ops.pebble.ExecError) -> bool:
        """Return True if exc indicates a duplicate-column migration error."""
        combined = f"{exc.stderr or ''}{exc.stdout or ''}"
        return "Duplicate column name" in combined

    def _probe_and_recover(
        self, container: ops.model.Container, cmd: list
    ) -> None:
        """Probe db-sync after a timeout and stamp if a duplicate-column is found.

        MySQL was slow on the first attempt; now that the connection is warm,
        probe with a short timeout to detect whether DDL auto-committed.
        Stamps the partial migration if detected; otherwise no-ops so the
        caller falls through to tenacity retries.
        """
        try:
            self._exec_db_sync(container, cmd, 60)
        except ops.pebble.ExecError as e:
            if self._is_duplicate_column_error(e):
                logger.warning(
                    "Duplicate column after timeout-probe; recovering. "
                    "stderr: %s",
                    e.stderr,
                )
                self._stamp_partial_migration(container, cmd)
            else:
                logger.warning(
                    "db-sync probe ExecError: stderr=%s stdout=%s",
                    e.stderr,
                    e.stdout,
                )
        except (ops.pebble.TimeoutError, ops.pebble.ChangeError):
            pass  # still slow; caller falls through to tenacity retries

    def _recover_exec_error(
        self,
        exc: ops.pebble.ExecError,
        container: ops.model.Container,
        cmd: list,
    ) -> None:
        """Stamp if duplicate-column; raise BlockedExceptionError otherwise."""
        if self._is_duplicate_column_error(exc):
            logger.warning(
                "Duplicate column on first attempt; recovering via stamp. "
                "stderr: %s",
                exc.stderr,
            )
            self._stamp_partial_migration(container, cmd)
        else:
            logger.warning(
                "db-sync ExecError: stderr=%s stdout=%s",
                exc.stderr,
                exc.stdout,
            )
            raise sunbeam_guard.BlockedExceptionError("DB sync failed")

    @sunbeam_job_ctrl.run_once_per_unit("db-sync")
    def run_db_sync(self) -> None:
        """Run DB sync with automatic recovery from partial migrations.

        If a previous db-sync timed out partway through, the DDL may have
        auto-committed in MySQL while alembic_version was never updated.  On
        the next attempt alembic fails immediately with
        "Duplicate column name '<col>'".

        To recover without operator intervention this method:
          1. Attempts one direct exec to capture failures immediately.
          2. On TimeoutError: probes once (short timeout) to detect the
             duplicate-column state while MySQL is still connected.
          3. On a Duplicate-column ExecError: calls ``_stamp_partial_migration``
             to advance alembic_version to the partially-applied revision.
          4. All other transient errors fall through to tenacity retries.
        """
        if not self.unit.is_leader():
            logger.info("Not lead unit, skipping DB sync")
            return

        cmd = self.db_sync_cmds[0]
        container = self.unit.get_container(self.db_sync_container_name)

        try:
            self._exec_db_sync(container, cmd, self.db_sync_timeout)
            return  # success on first attempt
        except ops.pebble.TimeoutError:
            logger.warning(
                "db-sync timed out; probing for partial migration state"
            )
            self._probe_and_recover(container, cmd)
        except ops.pebble.ExecError as e:
            self._recover_exec_error(e, container, cmd)
        except ops.pebble.ChangeError as e:
            logger.warning("db-sync ChangeError on first attempt: %s", e)

        try:
            self._retry_db_sync(cmd)
        except tenacity.RetryError:
            raise sunbeam_guard.BlockedExceptionError("DB sync failed")

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.open_ports()
        self.configure_containers()
        self.run_db_sync()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        for container in [
            OCTAVIA_API_CONTAINER,
            OCTAVIA_CONTROLLER_CONTAINER,
        ]:
            ph = self.get_named_pebble_handler(container)
            ph.execute(
                [
                    "chown",
                    f"{self.service_user}:{self.service_group}",
                    OCTAVIA_AGENT_SOCKET_DIR,
                ]
            )
        self._set_lbmgmt_ip()
        self._reconcile_amphora_containers()
        self._state.unit_bootstrapped = True

    def _reconcile_amphora_containers(self) -> None:
        """Start or stop Amphora services in octavia-controller based on config.

        When amphora-network-attachment is set AND both Amphora TLS cert
        relations are ready, the health-manager and worker pebble services are
        started inside octavia-controller.  When either condition is unmet they
        are stopped so they do not crash-loop on missing cert files.
        """
        ph = self.get_named_pebble_handler(OCTAVIA_CONTROLLER_CONTAINER)
        if not ph.pebble_ready:
            return
        amphora_ready = (
            bool(self.config.get("amphora-network-attachment"))
            and self.amphora_issuing_ca.ready
            and self.amphora_controller_cert.ready
        )
        if amphora_ready:
            ph.start_amphora_services()
        else:
            ph.stop_amphora_services()

    @staticmethod
    def _get_first_cert(handler):
        """Return the first non-None certificate from a TLS handler, or None."""
        for _cn, cert in handler.get_certs():
            if cert is not None:
                return cert
        return None

    def _store_amphora_certs_secret(self, content: dict) -> None:
        """Create or update the Juju app secret holding Amphora private keys.

        Raises ModelError on failure so the caller can decide whether to abort.
        """
        secret_id = self.peers.get_app_data(
            AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY
        )
        if secret_id:
            secret = self.model.get_secret(id=secret_id)
            secret.set_content(content)
        else:
            secret = self.model.app.add_secret(
                content, label=AMPHORA_CERTS_PRIVATE_KEYS_LABEL
            )
            self.peers.set_app_data(
                {AMPHORA_CERTS_PRIVATE_KEYS_SECRET_ID_KEY: secret.id}
            )

    def _sync_amphora_certs_to_peer_databag(self) -> None:
        """Share Amphora cert material to the peer app databag for non-leader units.

        Called on every configure_app_leader() invocation (leader only).  Reads
        certificate data from the TLS handlers and writes:
          - Public cert PEM strings to the peer app databag.
          - Private keys to a Juju app secret whose ID is also stored in the
            peer app databag.

        Non-leader units read this data from AmphoraCertificatesContext.context()
        so their pebble containers receive the same certificate files as the leader.

        If either cert relation has not yet delivered certificates, this method
        returns without writing anything—non-leader contexts will return an
        empty dict until the leader populates the databag.
        """
        issuing_cert = self._get_first_cert(self.amphora_issuing_ca)
        controller_cert = self._get_first_cert(self.amphora_controller_cert)

        if issuing_cert is None or controller_cert is None:
            logger.debug(
                "Amphora certs not yet available; skipping peer databag sync"
            )
            return

        private_keys_content = {
            "issuing-ca-private-key": self.amphora_issuing_ca.get_private_key()
            or "",
            "controller-cert-private-key": self.amphora_controller_cert.get_private_key()
            or "",
        }
        try:
            self._store_amphora_certs_secret(private_keys_content)
        except ModelError as e:
            logger.error(
                "Failed to store Amphora cert private-keys secret: %s", e
            )
            return

        self.peers.set_app_data(
            {
                AMPHORA_ISSUING_CACERT_PEER_KEY: str(issuing_cert.certificate),
                AMPHORA_ISSUING_CA_ROOT_PEER_KEY: (
                    str(issuing_cert.ca) if issuing_cert.ca else ""
                ),
                AMPHORA_CONTROLLER_CACERT_PEER_KEY: (
                    str(controller_cert.ca) if controller_cert.ca else ""
                ),
                AMPHORA_CONTROLLER_CERT_PEER_KEY: str(
                    controller_cert.certificate
                ),
            }
        )
        logger.debug("Synced Amphora cert data to peer app databag")

    def configure_app_leader(self, event: ops.framework.EventBase) -> None:
        """Run global app setup.

        Leader-only tasks including generating the shared heartbeat key.
        The key is only created when Amphora is enabled.
        """
        super().configure_app_leader(event)
        if self.config.get("amphora-network-attachment"):
            self.generate_heartbeat_key()
        self._sync_amphora_certs_to_peer_databag()


if __name__ == "__main__":  # pragma: nocover
    ops.main(OctaviaOperatorCharm)
