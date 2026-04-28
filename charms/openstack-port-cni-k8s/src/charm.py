#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""OpenStack Port CNI Charm.

Deploys two sets of Kubernetes manifests:

1. **ovs-cni** (upstream) — the OVS CNI DaemonSet that exposes Open vSwitch
   bridges as node resources and installs the ``ovs`` CNI binary.

2. **openstack-port-cni** — a thin/thick CNI pair where:
   - an initContainer copies the thin ``openstack-port-cni`` binary to every
     node's ``/opt/cni/bin/``;
   - a DaemonSet runs ``openstack-port-daemon``, which holds OpenStack
     credentials and manages Neutron ports via a Unix socket.

OpenStack credentials are obtained from a Keystone integration (the
``identity-credentials`` relation) and stored in a Kubernetes Secret that the
DaemonSet mounts via ``envFrom``. An optional CA certificate from the
``receive-ca-cert`` relation is stored in a separate Secret and mounted
into the DaemonSet, with ``OS_CACERT`` set accordingly.
"""

import base64
import hashlib
import json
import logging

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing
from manifests import (
    OpenstackPortCniManifests,
    OvsCniManifests,
)
from ops.main import (
    main,
)
from ops.manifests import (
    Collector,
    ManifestClientError,
)
from ops.model import (
    MaintenanceStatus,
)

log = logging.getLogger(__name__)

# Kubernetes Secret that holds OS_* credentials for the daemon.
_CREDS_SECRET_NAME = "openstack-port-cni-credentials"
_CREDS_SECRET_NAMESPACE = "kube-system"

# Kubernetes Secret that holds the CA bundle for the OpenStack endpoints.
_CA_BUNDLE_SECRET_NAME = "openstack-port-cni-ca-bundle"
_CA_BUNDLE_KEY = "ca-bundle.pem"
_CA_BUNDLE_PATH = "/etc/openstack-port-cni/ca-bundle.pem"

# DaemonSet managed by this charm.
_DAEMON_DAEMONSET_NAME = "openstack-port-daemon"
_OVS_CNI_DAEMONSET_NAME = "ovs-cni-amd64"


@sunbeam_tracing.trace_sunbeam_charm
class OpenstackPortCniCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Deploy and manage ovs-cni and openstack-port-cni manifests."""

    service_name = "openstack-port-cni"

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)

        self.ovs_cni = OvsCniManifests(self, self.config)
        self.openstack_port_cni = OpenstackPortCniManifests(self, self.config)
        self.collector = Collector(self.ovs_cni, self.openstack_port_cni)

        self._daemonsets_status = compound_status.Status(
            "daemonsets", priority=10
        )
        self.status_pool.add(self._daemonsets_status)

        self.framework.observe(self.on.remove, self._on_remove)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(
            self.on.identity_credentials_relation_broken,
            self._on_identity_credentials_broken,
        )
        self.framework.observe(
            self.on.collect_app_status, self._on_collect_app_status_event
        )

        # --- Actions ---
        self.framework.observe(
            self.on.list_versions_action, self._list_versions
        )
        self.framework.observe(
            self.on.list_resources_action, self._list_resources
        )
        self.framework.observe(
            self.on.scrub_resources_action, self._scrub_resources
        )
        self.framework.observe(
            self.on.sync_resources_action, self._sync_resources
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _k8s_client(self):
        """Return a lightkube Client scoped to the cluster."""
        from lightkube import (
            Client,
        )

        return Client()

    # ------------------------------------------------------------------
    # CA bundle helpers
    # ------------------------------------------------------------------

    def _get_ca_bundle(self) -> str:
        """Collect CA PEM data from all receive-ca-cert relation units."""
        ca_bundle = []
        seen = set()
        for relation in self.model.relations.get("receive-ca-cert", []):
            for unit in relation.units:
                data = relation.data[unit]
                ca = data.get("ca", "")
                if ca and ca not in seen:
                    ca_bundle.append(ca)
                    seen.add(ca)
                try:
                    chain_list = json.loads(data.get("chain", "[]"))
                except (ValueError, TypeError):
                    chain_list = []
                for cert in chain_list:
                    if cert and cert not in seen:
                        ca_bundle.append(cert)
                        seen.add(cert)
        return "\n".join(ca_bundle)

    def _sync_ca_bundle(self) -> None:
        """Create or update the CA bundle Secret in kube-system.

        When no CA is available (relation absent or empty), the Secret is
        deleted so the optional volume mount in the DaemonSet stays empty and
        gophercloud falls back to the system trust store.
        """
        from lightkube.core.exceptions import (
            ApiError,
        )
        from lightkube.models.meta_v1 import (
            ObjectMeta,
        )
        from lightkube.resources.core_v1 import (
            Secret,
        )

        ca_bundle = self._get_ca_bundle()
        client = self._k8s_client()

        if not ca_bundle:
            try:
                client.delete(
                    Secret,
                    _CA_BUNDLE_SECRET_NAME,
                    namespace=_CREDS_SECRET_NAMESPACE,
                )
                log.info("CA bundle Secret removed")
            except ApiError as exc:
                if exc.status.code != 404:
                    log.error("Failed to delete CA bundle Secret: %s", exc)
            return

        secret = Secret(
            metadata=ObjectMeta(
                name=_CA_BUNDLE_SECRET_NAME,
                namespace=_CREDS_SECRET_NAMESPACE,
            ),
            data={
                _CA_BUNDLE_KEY: base64.b64encode(ca_bundle.encode()).decode()
            },
        )
        try:
            client.apply(
                secret, field_manager="openstack-port-cni", force=True
            )
            log.info("CA bundle Secret updated")
        except Exception as exc:
            raise sunbeam_guard.WaitingExceptionError(
                f"Failed to update CA bundle Secret: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Kubernetes Secret for OpenStack credentials
    # ------------------------------------------------------------------

    def _sync_openstack_credentials(self) -> None:
        """Create or update the Kubernetes Secret with OpenStack credentials.

        The Secret is consumed by the openstack-port-daemon container via
        ``envFrom.secretRef``.  Only the leader writes to the cluster.
        """
        try:
            auth_url = self.ccreds.interface.internal_endpoint
            username = self.ccreds.interface.username
            password = self.ccreds.interface.password
            project_name = self.ccreds.interface.project_name
            project_id = self.ccreds.interface.project_id
            user_domain_name = (
                self.ccreds.interface.user_domain_name or "service_domain"
            )
            project_domain_name = (
                self.ccreds.interface.project_domain_name or "service_domain"
            )
        except (AttributeError, KeyError, TypeError):
            raise sunbeam_guard.WaitingExceptionError(
                "Keystone credentials not yet fully available"
            )

        if not all([auth_url, username, password, project_name, project_id]):
            raise sunbeam_guard.BlockedExceptionError(
                "Keystone credentials incomplete"
            )

        def _b64(value: str) -> str:
            return base64.b64encode(value.encode()).decode()

        region = str(self.config.get("region", "RegionOne"))
        creds = {
            "OS_AUTH_URL": auth_url,
            "OS_USERNAME": username,
            "OS_PASSWORD": password,
            "OS_PROJECT_NAME": project_name,
            "OS_PROJECT_ID": project_id,
            # gophercloud's AuthOptionsFromEnv reads OS_DOMAIN_NAME (not
            # OS_USER_DOMAIN_NAME) to set DomainName for username auth.
            "OS_DOMAIN_NAME": user_domain_name,
            "OS_USER_DOMAIN_NAME": user_domain_name,
            "OS_PROJECT_DOMAIN_NAME": project_domain_name,
            "OS_IDENTITY_API_VERSION": "3",
            "OS_REGION_NAME": region,
        }
        # Include OS_CACERT only when a CA bundle is present so that
        # gophercloud uses the system trust store when no CA is configured.
        if self._get_ca_bundle():
            creds["OS_CACERT"] = _CA_BUNDLE_PATH

        # Checksum used to trigger a DaemonSet rolling restart when credentials change.
        creds_hash = hashlib.sha256(
            "|".join(f"{k}={v}" for k, v in sorted(creds.items())).encode()
        ).hexdigest()[:16]

        from lightkube.models.meta_v1 import (
            ObjectMeta,
        )
        from lightkube.resources.core_v1 import (
            Secret,
        )

        secret = Secret(
            metadata=ObjectMeta(
                name=_CREDS_SECRET_NAME,
                namespace=_CREDS_SECRET_NAMESPACE,
            ),
            data={k: _b64(v) for k, v in creds.items()},
        )
        try:
            self._k8s_client().apply(
                secret, field_manager="openstack-port-cni", force=True
            )
            log.info("OpenStack credentials Secret updated")
        except Exception as exc:
            raise sunbeam_guard.WaitingExceptionError(
                f"Failed to update OpenStack credentials Secret: {exc}"
            ) from exc

        self._patch_daemon_checksum(creds_hash)

    def _patch_daemon_checksum(self, creds_hash: str) -> None:
        """Patch the openstack-port-daemon pod template with a credentials checksum.

        When the checksum annotation changes, the DaemonSet controller performs
        a rolling restart so pods pick up the new OS_* environment variables.

        Uses a raw-dict strategic-merge patch to avoid constructing a full
        DaemonSetSpec object (lightkube requires selector to be non-None).
        """
        from lightkube.core.exceptions import (
            ApiError,
        )
        from lightkube.resources.apps_v1 import (
            DaemonSet,
        )
        from lightkube.types import (
            PatchType,
        )

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"checksum/credentials": creds_hash}
                    }
                }
            }
        }
        try:
            self._k8s_client().patch(
                DaemonSet,
                _DAEMON_DAEMONSET_NAME,
                patch,
                namespace=_CREDS_SECRET_NAMESPACE,
                patch_type=PatchType.MERGE,
            )
            log.info(
                "DaemonSet patched with credentials checksum %s", creds_hash
            )
        except ApiError as exc:
            if exc.status.code == 404:
                log.debug("DaemonSet not yet present, skipping checksum patch")
            else:
                log.error("Failed to patch DaemonSet checksum: %s", exc)
        except Exception as exc:
            log.error("Failed to patch DaemonSet checksum: %s", exc)

    def _delete_openstack_credentials_secret(self) -> None:
        """Remove the OpenStack credentials Secret from the cluster."""
        from lightkube.core.exceptions import (
            ApiError,
        )
        from lightkube.resources.core_v1 import (
            Secret,
        )

        try:
            self._k8s_client().delete(
                Secret,
                _CREDS_SECRET_NAME,
                namespace=_CREDS_SECRET_NAMESPACE,
            )
            log.info("OpenStack credentials Secret deleted")
        except ApiError as exc:
            if exc.status.code != 404:
                log.error(
                    "Failed to delete OpenStack credentials Secret: %s", exc
                )

    # ------------------------------------------------------------------
    # ops-sunbeam lifecycle
    # ------------------------------------------------------------------

    def configure_app_leader(self, event: ops.EventBase) -> None:
        """Sync credentials/CA bundle and apply manifests (leader only)."""
        super().configure_app_leader(event)

        self._sync_ca_bundle()
        self._sync_openstack_credentials()

        log.info("Applying ovs-cni manifests")
        try:
            self.ovs_cni.apply_manifests()
        except ManifestClientError:
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for kube-apiserver"
            )

        log.info("Applying openstack-port-cni manifests")
        try:
            self.openstack_port_cni.apply_manifests()
        except ManifestClientError:
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for kube-apiserver"
            )

    def _on_identity_credentials_broken(
        self, event: ops.RelationBrokenEvent
    ) -> None:
        """Delete the credentials Secret when the identity-credentials relation is removed."""
        if self.unit.is_leader():
            self._delete_openstack_credentials_secret()

    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Re-check DaemonSet readiness and update the status pool entry."""
        waiting = self._daemonset_waiting()
        if waiting:
            self._daemonsets_status.set(ops.WaitingStatus(waiting))
        else:
            self._daemonsets_status.set(ops.ActiveStatus())

    def _on_collect_app_status_event(
        self, event: ops.CollectStatusEvent
    ) -> None:
        """Publish app status via the compound status pool (leader only)."""
        status = self.status_pool.compute_status()
        if status:
            event.add_status(status)

    def _daemonset_waiting(self) -> str:
        """Return a wait message if any managed DaemonSet pods are not all ready.

        Queries the live DaemonSet status so that a pod-level failure (e.g.
        missing Secret, image pull error) surfaces as WaitingStatus rather
        than being masked by a successful manifest apply.  Returns an empty
        string when all desired pods across all DaemonSets are ready.
        """
        from lightkube.core.exceptions import (
            ApiError,
        )
        from lightkube.resources.apps_v1 import (
            DaemonSet,
        )

        daemonsets = [
            (_DAEMON_DAEMONSET_NAME, _CREDS_SECRET_NAMESPACE),
            (_OVS_CNI_DAEMONSET_NAME, _CREDS_SECRET_NAMESPACE),
        ]
        client = self._k8s_client()
        for ds_name, ns in daemonsets:
            try:
                ds = client.get(DaemonSet, ds_name, namespace=ns)
            except ApiError as exc:
                if exc.status.code == 404:
                    return f"DaemonSet {ds_name} not yet present"
                log.warning("Could not query DaemonSet status: %s", exc)
                continue
            except Exception as exc:
                log.warning("Could not query DaemonSet status: %s", exc)
                continue

            status = ds.status
            if status is None:
                return f"DaemonSet {ds_name} status unavailable"

            desired = status.desiredNumberScheduled or 0
            ready = status.numberReady or 0
            if desired == 0:
                continue
            if ready < desired:
                return f"DaemonSet {ds_name}: {ready}/{desired} pods ready"
        return ""

    def post_config_setup(self) -> None:
        """Set workload version and active status after successful configure."""
        unready = self.collector.unready
        if unready:
            raise sunbeam_guard.WaitingExceptionError(", ".join(unready))

        self.status.set(ops.ActiveStatus())

    def _on_remove(self, event: ops.EventBase) -> None:
        """Remove all managed resources from the cluster."""
        if not self.unit.is_leader():
            return

        self._delete_openstack_credentials_secret()

        from lightkube.core.exceptions import (
            ApiError,
        )
        from lightkube.resources.core_v1 import Secret as _Secret

        try:
            self._k8s_client().delete(
                _Secret,
                _CA_BUNDLE_SECRET_NAME,
                namespace=_CREDS_SECRET_NAMESPACE,
            )
        except ApiError as exc:
            if exc.status.code != 404:
                log.error("Failed to delete CA bundle Secret: %s", exc)

        try:
            self.openstack_port_cni.delete_manifests(
                ignore_unauthorized=True, ignore_not_found=True
            )
            self.ovs_cni.delete_manifests(
                ignore_unauthorized=True, ignore_not_found=True
            )
        except ManifestClientError as exc:
            log.error("Failed to remove manifests: %s", exc)
            self.unit.status = MaintenanceStatus("Failed to remove manifests")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _list_versions(self, event) -> None:
        self.collector.list_versions(event)

    def _list_resources(self, event) -> None:
        resources = event.params.get("resources", "")
        self.collector.list_resources(event, resources=resources)  # type: ignore[call-arg]

    def _scrub_resources(self, event) -> None:
        resources = event.params.get("resources", "")
        self.collector.scrub_resources(event, resources=resources)  # type: ignore[call-arg]

    def _sync_resources(self, event) -> None:
        resources = event.params.get("resources", "")
        try:
            self.collector.apply_missing_resources(event, resources=resources)  # type: ignore[call-arg]
        except ManifestClientError as exc:
            msg = "Failed to sync missing resources: " + " -> ".join(
                map(str, exc.args)
            )
            event.set_results({"result": msg})


if __name__ == "__main__":
    main(OpenstackPortCniCharm)  # pragma: no cover
