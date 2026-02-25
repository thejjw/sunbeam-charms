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

import base64
import binascii
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
from lightkube.core.client import (
    Client,
)
from lightkube.core.exceptions import (
    ApiError,
)
from lightkube.resources.apps_v1 import (
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
OCTAVIA_DRIVER_AGENT_CONTAINER = "octavia-driver-agent"
OCTAVIA_HOUSEKEEPING_CONTAINER = "octavia-housekeeping"
OCTAVIA_HEALTH_MANAGER_CONTAINER = "octavia-health-manager"
OCTAVIA_WORKER_CONTAINER = "octavia-worker"
OCTAVIA_AGENT_SOCKET_DIR = "/var/run/octavia"
OCTAVIA_HEALTH_MANAGER_PORT = 5555
OCTAVIA_HEARTBEAT_KEY = "heartbeat-key"

_VALID_TOPOLOGY = frozenset({"SINGLE", "ACTIVE_STANDBY"})
# "auto" is a charm-internal value: it maps to soft-anti-affinity for
# ACTIVE_STANDBY and disables anti-affinity for SINGLE topology.
_VALID_ANTI_AFFINITY = frozenset(
    {"anti-affinity", "soft-anti-affinity", "auto", "disable"}
)
_VALID_LOG_PROTOCOL = frozenset({"UDP", "TCP"})


@sunbeam_tracing.trace_type
class OctaviaDriverAgentPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Octavia Driver Agent."""

    def get_layer(self) -> dict:
        """Octavia Driver Agent service layer.

        :returns: pebble layer configuration for driver agent service
        :rtype: dict
        """
        return {
            "summary": "octavia driver agent layer",
            "description": "pebble configuration for octavia-driver-agent service",
            "services": {
                "octavia-driver-agent": {
                    "override": "replace",
                    "summary": "Octavia Driver Agent",
                    "command": "octavia-driver-agent",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_type
class OctaviaHousekeepingPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Octavia Housekeeping."""

    def get_layer(self) -> dict:
        """Octavia Housekeeping service layer.

        :returns: pebble layer configuration for housekeeping service
        :rtype: dict
        """
        return {
            "summary": "octavia housekeeping layer",
            "description": "pebble configuration for octavia-housekeeping service",
            "services": {
                "octavia-housekeeping": {
                    "override": "replace",
                    "summary": "Octavia Housekeeping",
                    "command": "octavia-housekeeping",
                    "startup": "enabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }


@sunbeam_tracing.trace_type
class OctaviaHealthManagerPebbleHandler(
    sunbeam_chandlers.ServicePebbleHandler
):
    """Pebble handler for Octavia Health manager."""

    def get_layer(self) -> dict:
        """Octavia Health manager service layer.

        :returns: pebble layer configuration for health manager service
        :rtype: dict
        """
        return {
            "summary": "octavia health manager layer",
            "description": "pebble configuration for octavia-health-manager service",
            "services": {
                "octavia-health-manager": {
                    "override": "replace",
                    "summary": "Octavia Health manager",
                    "command": "octavia-health-manager",
                    # startup: disabled — this service is optional and only
                    # started by _reconcile_amphora_containers when
                    # amphora-network-attachment is configured.  Using
                    # 'disabled' prevents pebble from auto-starting it on
                    # pod restarts when Amphora is not in use.
                    "startup": "disabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Write config files but defer service start to _reconcile_amphora_containers.

        When Amphora is not configured we still write config files so that
        octavia.conf is always up to date, but we do not start the service.
        """
        if not self.charm.config.get("amphora-network-attachment"):
            self.configure_container(context)
            self.status.set(ops.ActiveStatus(""))
            return
        super().init_service(context)

    @property
    def service_ready(self) -> bool:
        """Whether the service is ready.

        When amphora-network-attachment is not configured the container
        is not required, so it is treated as always ready to avoid
        blocking the unit on an optional service.
        """
        if not self.charm.config.get("amphora-network-attachment"):
            return True
        return super().service_ready


@sunbeam_tracing.trace_type
class OctaviaWorkerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Octavia Worker."""

    def get_layer(self) -> dict:
        """Octavia Worker service layer.

        :returns: pebble layer configuration for worker service
        :rtype: dict
        """
        return {
            "summary": "octavia worker layer",
            "description": "pebble configuration for octavia-worker service",
            "services": {
                "octavia-worker": {
                    "override": "replace",
                    "summary": "Octavia Worker",
                    "command": "octavia-worker",
                    # startup: disabled — see OctaviaHealthManagerPebbleHandler
                    # for rationale.
                    "startup": "disabled",
                    "user": self.charm.service_user,
                    "group": self.charm.service_group,
                }
            },
        }

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Write config files but defer service start to _reconcile_amphora_containers."""
        if not self.charm.config.get("amphora-network-attachment"):
            self.configure_container(context)
            self.status.set(ops.ActiveStatus(""))
            return
        super().init_service(context)

    @property
    def service_ready(self) -> bool:
        """Whether the service is ready.

        When amphora-network-attachment is not configured the container
        is not required, so it is treated as always ready to avoid
        blocking the unit on an optional service.
        """
        if not self.charm.config.get("amphora-network-attachment"):
            return True
        return super().service_ready


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
class AmphoraCertificatesContext(sunbeam_config_contexts.ConfigContext):
    """Amphora Certificates configuration context.

    Decodes base64-encoded certificate config options before passing
    to templates.
    """

    def _decode_cert(self, config_key: str) -> str | None:
        """Decode a single base64-encoded certificate config option.

        :param config_key: The charm config key to read and decode.
        :returns: Decoded PEM string, or None if unset or invalid.
        :rtype: str | None
        """
        value = self.charm.config.get(config_key)
        if not value:
            return None
        try:
            return base64.b64decode(value).decode("utf-8")
        except (ValueError, binascii.Error) as e:
            logger.error(f"Failed to decode {config_key}: {e}")
            return None

    def context(self) -> dict:
        """Configuration context for Amphora certificates."""
        cert_keys = {
            "lb_mgmt_controller_cacert": "lb-mgmt-controller-cacert",
            "lb_mgmt_issuing_cacert": "lb-mgmt-issuing-cacert",
            "lb_mgmt_issuing_ca_private_key": "lb-mgmt-issuing-ca-private-key",
            "lb_mgmt_controller_cert": "lb-mgmt-controller-cert",
        }
        return {
            ctx_key: decoded
            for ctx_key, config_key in cert_keys.items()
            if (decoded := self._decode_cert(config_key)) is not None
        }


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
        # Priority 50 sits below the workload status (100) so workload issues
        # still win, but it will surface when everything else is fine.
        self.amphora_net_status = compound_status.Status(
            "amphora-network", priority=50
        )
        self.status_pool.add(self.amphora_net_status)

        # Status slot for configuration validation errors (priority 95 so it
        # surfaces above amphora-network (50) and above the base class
        # bootstrap_status (90), but below the workload status (100)).
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
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                OCTAVIA_API_CONTAINER,
                self.service_name,
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            OctaviaHousekeepingPebbleHandler(
                self,
                OCTAVIA_HOUSEKEEPING_CONTAINER,
                "octavia-housekeeping",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            OctaviaDriverAgentPebbleHandler(
                self,
                OCTAVIA_DRIVER_AGENT_CONTAINER,
                "octavia-driver-agent",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            OctaviaHealthManagerPebbleHandler(
                self,
                OCTAVIA_HEALTH_MANAGER_CONTAINER,
                "octavia-health-manager",
                self.default_container_configs(),
                self.template_dir,
                self.configure_charm,
            ),
            OctaviaWorkerPebbleHandler(
                self,
                OCTAVIA_WORKER_CONTAINER,
                "octavia-worker",
                self.default_container_configs(),
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
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/octavia.conf",
                self.service_user,
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/api_audit_map.conf",
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
            sunbeam_core.ContainerConfigFile(
                "/etc/octavia/certs/controller_ca.pem",
                self.service_user,
                self.service_group,
                0o640,
            ),
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
        # Re-detect the management network IP in case the pod was recreated.
        self._set_lbmgmt_ip()
        # ops-sunbeam doesn't wire upgrade_charm -> configure_charm by default.
        self.configure_charm(event)

    def _read_pod_network_status(self, pod_name: str) -> list | None:
        """Fetch and parse k8s.v1.cni.cncf.io/network-status from the pod.

        :returns: parsed list of network-status entries, or None on error
        """
        namespace = self.model.name
        try:
            client = Client()
            pod = client.get(Pod, name=pod_name, namespace=namespace)
        except ApiError as e:
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
        except json.JSONDecodeError as e:
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
        lbmgmt_ip = self._get_lbmgmt_ip_from_network_status()
        if lbmgmt_ip:
            self.peers.set_unit_data({"lbmgmt-ip": lbmgmt_ip})
            logger.info(f"Set lbmgmt-ip to {lbmgmt_ip}")
            self.amphora_net_status.set(ops.ActiveStatus(""))
        elif network_attachment:
            # The operator has configured a network attachment but the
            # interface is not yet available (pod may still be rolling).
            msg = "Amphora management network interface not detected"
            logger.warning(msg)
            self.amphora_net_status.set(ops.WaitingStatus(msg))
        else:
            # No network attachment configured — nothing expected, no issue.
            self.amphora_net_status.set(ops.ActiveStatus(""))

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
        if self.config.get("amphora-network-attachment"):
            required_certs = [
                "lb-mgmt-issuing-cacert",
                "lb-mgmt-issuing-ca-private-key",
                "lb-mgmt-issuing-ca-key-passphrase",
                "lb-mgmt-controller-cacert",
                "lb-mgmt-controller-cert",
            ]
            missing = [c for c in required_certs if not self.config.get(c)]
            if missing:
                errors.append(
                    "Amphora certificates not configured: "
                    + ", ".join(missing)
                )
            else:
                # All cert values are present — validate that the base64-
                # encoded ones are actually decodable so we surface a clear
                # BlockedStatus rather than silently writing empty cert files.
                b64_certs = [
                    "lb-mgmt-issuing-cacert",
                    "lb-mgmt-issuing-ca-private-key",
                    "lb-mgmt-controller-cacert",
                    "lb-mgmt-controller-cert",
                ]
                invalid = []
                for key in b64_certs:
                    value = self.config.get(key, "")
                    try:
                        base64.b64decode(value)
                    except (ValueError, binascii.Error):
                        invalid.append(key)
                if invalid:
                    errors.append(
                        "Amphora certificates contain invalid base64: "
                        + ", ".join(invalid)
                    )
        return errors

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Catchall handler to configure charm services.

        Validates configuration before delegating to the base class.
        Blocks if required config values are missing or invalid, and
        waits if the barbican-service relation is not yet ready.
        """
        errors = self._get_config_errors()
        if errors:
            self.config_status.set(ops.BlockedStatus(errors[0]))
            return
        if self.config.get("amphora-network-attachment"):
            if not self.model.relations.get("barbican-service"):
                self.config_status.set(
                    ops.BlockedStatus(
                        "barbican-service integration required for Amphora"
                    )
                )
                return
            if not self.barbican_svc.ready:
                self.config_status.set(
                    ops.WaitingStatus("Waiting for barbican-service")
                )
                return
        self.config_status.set(ops.ActiveStatus(""))
        super().configure_charm(event)

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
            parts = out.strip().split()
            current_rev = parts[0] if parts else ""
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
            OCTAVIA_DRIVER_AGENT_CONTAINER,
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
        """Start or stop Amphora containers based on config.

        When amphora-network-attachment is set the health-manager and worker
        containers are started. When it is unset they are stopped so they do
        not consume resources unnecessarily.
        """
        amphora_enabled = bool(self.config.get("amphora-network-attachment"))
        for container_name in [
            OCTAVIA_HEALTH_MANAGER_CONTAINER,
            OCTAVIA_WORKER_CONTAINER,
        ]:
            ph = self.get_named_pebble_handler(container_name)
            if not ph.pebble_ready:
                continue
            if amphora_enabled:
                ph.start_all()
            else:
                ph.stop_all()

    def configure_app_leader(self, event: ops.framework.EventBase) -> None:
        """Run global app setup.

        Leader-only tasks including generating the shared heartbeat key.
        The key is only created when Amphora is enabled.
        """
        super().configure_app_leader(event)
        if self.config.get("amphora-network-attachment"):
            self.generate_heartbeat_key()


if __name__ == "__main__":  # pragma: nocover
    ops.main(OctaviaOperatorCharm)
