#!/usr/bin/env python3

# Copyright 2024 Canonical Ltd.
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


"""Sunbeam Clusterd Charm.

This charm manages a clusterd deployment. Clusterd is a service storing
every metadata about a sunbeam deployment.
"""

import hashlib
import logging
import socket
from pathlib import (
    Path,
)

import clusterd
import ops
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import requests
import tenacity
from charms.operator_libs_linux.v2 import (
    snap,
)
from cryptography import (
    x509,
)
from ops_sunbeam.relation_handlers import (
    RelationHandler,
)
from relation_handlers import (
    ClusterdNewNodeEvent,
    ClusterdNodeAddedEvent,
    ClusterdPeerHandler,
    ClusterdRemoveNodeEvent,
)

logger = logging.getLogger(__name__)


def _identity(x: bool) -> bool:
    return x


class SnapInstallationError(Exception):
    """Custom exception for snap installation failure errors."""


@sunbeam_tracing.trace_sunbeam_charm
class SunbeamClusterdCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.StoredState()
    service_name = "sunbeam-clusterd"
    clusterd_port = 7000

    def __init__(self, framework: ops.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self._state.set_default(
            channel="config", departed=False, certs_hash=""
        )
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.stop, self._on_stop)
        self.framework.observe(
            self.on.get_credentials_action, self._on_get_credentials_action
        )
        self._clusterd = clusterd.ClusterdClient(
            Path("/var/snap/openstack/common/state/control.socket")
        )

    def get_relation_handlers(
        self, handlers: list[RelationHandler] | None = None
    ) -> list[RelationHandler]:
        """Setup charm relation handlers."""
        handlers = handlers or []
        if self.can_add_handler("peers", handlers):
            self.peers = ClusterdPeerHandler(
                self,
                "peers",
                self.configure_charm,
                "peers" in self.mandatory_relations,
            )
            handlers.append(self.peers)
        if self.can_add_handler("certificates", handlers):
            self.certs = sunbeam_rhandlers.TlsCertificatesHandler(
                self,
                "certificates",
                self.configure_charm,
                sans_dns=self.get_sans_dns(),
                sans_ips=frozenset(self.get_sans_ips()),
                certificate_requests=self.get_tls_certificate_requests(),
                app_managed_certificates=True,
                mandatory="certificates" in self.mandatory_relations,
            )
            handlers.append(self.certs)
        return super().get_relation_handlers(handlers)

    def get_tls_certificate_requests(self) -> list:
        """Get TLS certificate requests for the service."""
        from charms.tls_certificates_interface.v4.tls_certificates import (
            CertificateRequestAttributes,
        )

        certificate_requests = [
            CertificateRequestAttributes(  # type: ignore[arg-type]
                common_name=self.app.name,
                sans_dns=self.get_domain_name_sans(),
                sans_ip=self.get_sans_ips(),
                additional_critical_extensions=[
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=True,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    x509.ExtendedKeyUsage({x509.OID_SERVER_AUTH}),
                ],
            ),
            CertificateRequestAttributes(  # type: ignore[arg-type]
                common_name=f"{self.app.name}-client",
                sans_dns=self.get_domain_name_sans(),
                sans_ip=self.get_sans_ips(),
                additional_critical_extensions=[
                    x509.KeyUsage(
                        digital_signature=True,
                        content_commitment=False,
                        key_encipherment=True,
                        data_encipherment=False,
                        key_agreement=False,
                        key_cert_sign=False,
                        crl_sign=False,
                        encipher_only=False,
                        decipher_only=False,
                    ),
                    x509.ExtendedKeyUsage({x509.OID_CLIENT_AUTH}),
                ],
            ),
        ]
        return certificate_requests

    def get_domain_name_sans(self) -> list[str]:
        """Return domain name sans."""
        return [socket.gethostname()]

    def get_sans_ips(self) -> list[str]:
        """Return Subject Alternate Names to use in cert for service."""
        return ["127.0.0.1"]

    def _on_install(self, event: ops.InstallEvent) -> None:
        """Handle install event."""
        try:
            with sunbeam_guard.guard(self, "Ensure snap installation", False):
                self.ensure_snap_present()
        except TimeoutError:
            logger.debug("Snap installation failed, retrying.")
            event.defer()
            return
        self.clusterd_ready()
        self.status.set(
            ops.WaitingStatus("Waiting for clusterd initialization")
        )

    def _on_stop(self, event: ops.StopEvent) -> None:
        """Handle stop event."""
        try:
            self._clusterd.shutdown()
        except clusterd.ClusterdUnavailableError:
            logger.debug("Clusterd not available, skipping shutdown.")
        snap.SnapCache()["openstack"].stop()

    def _on_get_credentials_action(self, event: ops.ActionEvent) -> None:
        """Handle get-credentials action."""
        if not self.peers.interface.state.joined:
            event.fail("Clusterd not joined yet")
            return

        credentials = {}
        if relation := self.model.get_relation(self.certs.relation_name):
            if relation.active:
                certificate_context = self.certs.get_certificate_context(
                    f"{self.app.name}-client"
                )
                credentials = {
                    "certificate-authority": certificate_context.get(
                        "ca_cert", ""
                    ),
                    "certificate": certificate_context.get("cert", ""),
                    "private-key-secret": certificate_context.get("key", ""),
                }
            if not credentials:
                event.fail("No credentials found yet")
                return

        event.set_results(
            {
                "url": "https://"
                + self._binding_address()
                + ":"
                + str(self.clusterd_port),
                **credentials,
            }
        )

    def _binding_address(self) -> str:
        """Return the binding address."""
        relation = self.model.get_relation("peers")

        if relation is None:
            raise ValueError("Missing relation peers")

        binding = self.model.get_binding(relation)

        if binding is None:
            raise ValueError("Missing binding peers")

        if binding.network.bind_address is None:
            raise ValueError("Missing binding address")

        return str(binding.network.bind_address)

    def ensure_snap_present(self):
        """Install/refresh snap if needed."""
        config = self.model.config.get
        snap_channel = config("snap-channel")

        try:
            cache = snap.SnapCache()
            openstack = cache["openstack"]
            if not openstack.present or snap_channel != openstack.channel:
                openstack.ensure(snap.SnapState.Latest, channel=snap_channel)
                self._state.channel = openstack.channel
                self.set_workload_version()
        except (snap.SnapError, snap.SnapNotFoundError) as e:
            logger.error(
                "An exception occurred when installing snap. Reason: %s",
                e.message,
            )
            raise SnapInstallationError("openstack snap installation failed")

    def set_workload_version(self):
        """Set workload version."""
        cache = snap.SnapCache()
        openstack = cache["openstack"]
        if not openstack.present:
            return
        version = openstack.channel + f"(rev {openstack.revision})"
        self.unit.set_workload_version(version)

    def configure_app_leader(self, event: ops.EventBase):
        """Configure leader unit."""
        if not self.clusterd_ready():
            logger.debug("Clusterd not ready yet.")
            event.defer()
            raise sunbeam_guard.WaitingExceptionError("Clusterd not ready yet")
        if not self.is_leader_ready():
            self.bootstrap_cluster()
            self.peers.interface.state.joined = True
        self.configure_certificates()
        super().configure_app_leader(event)
        if isinstance(event, ClusterdNewNodeEvent):
            self.add_node_to_cluster(event)

    def configure_app_non_leader(self, event: ops.EventBase):
        """Configure non-leader unit."""
        super().configure_app_non_leader(event)
        if isinstance(event, ClusterdNodeAddedEvent):
            self.join_node_to_cluster(event)

    def configure_unit(self, event: ops.EventBase):
        """Configure unit."""
        super().configure_unit(event)
        if isinstance(event, ClusterdRemoveNodeEvent):
            self.remove_node_from_cluster(event)
        self.ensure_snap_present()
        config = self.model.config.get
        snap_data = {
            "daemon.debug": config("debug", False),
        }
        self.set_snap_data(snap_data)

    def configure_certificates(self):
        """Configure certificates."""
        if not self.unit.is_leader():
            logger.debug("Not leader, skipping certificate configuration.")
            return
        if not self.certs.ready:
            logger.debug("Certificates not ready yet.")
            return
        certs = self.certs.context()
        certs_hash = hashlib.sha256(bytes(str(certs), "utf-8")).hexdigest()
        if certs_hash == self._state.certs_hash:
            logger.debug("Certificates have not changed.")
            return
        self._clusterd.set_certs(
            ca=certs["ca_cert"],
            key=certs["key"],
            cert=certs["cert"],
        )
        self._state.certs_hash = certs_hash

    def set_snap_data(self, snap_data: dict):
        """Set snap data on local snap."""
        cache = snap.SnapCache()
        openstack = cache["openstack"]
        new_settings = {}
        for k in sorted(snap_data.keys()):
            try:
                if snap_data[k] != openstack.get(k):
                    new_settings[k] = snap_data[k]
            except snap.SnapError:
                # Trying to retrieve an unset parameter results in a snapError
                # so assume the snap.SnapError means there is missing config
                # that needs setting.
                new_settings[k] = snap_data[k]
        if new_settings:
            logger.debug(f"Applying new snap settings {new_settings}")
            openstack.set(new_settings, typed=True)
        else:
            logger.debug("Snap settings do not need updating")

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(10),
        retry=(
            tenacity.retry_if_exception_type(clusterd.ClusterdUnavailableError)
            | tenacity.retry_if_not_result(_identity)
        ),
        after=tenacity.after_log(logger, logging.WARNING),
        wait=tenacity.wait_exponential(multiplier=1, min=1, max=30),
    )
    def clusterd_ready(self) -> bool:
        """Check whether clusterd is ready."""
        if not self._clusterd.ready():
            return False
        return True

    def bootstrap_cluster(self):
        """Bootstrap the cluster."""
        logger.info("Bootstrapping the cluster")
        self._clusterd.bootstrap(
            self.unit.name.replace("/", "-"),
            self._binding_address() + ":" + str(self.clusterd_port),
        )

    def add_node_to_cluster(self, event: ClusterdNewNodeEvent) -> None:
        """Generate token for node joining."""
        if event.unit is None:
            logger.debug("No unit to add")
            return
        unit_key = f"{event.unit.name}.join_token"
        if self.peers.get_app_data(unit_key):
            logger.debug(f"Already generated token for {event.unit.name}")
            return

        try:
            token = self._clusterd.generate_token(
                event.unit.name.replace("/", "-")
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code >= 500:
                logger.error(f"Clusterd error: {str(e)}")
                logger.debug("Failed to generate token, retrying.")
                event.defer()
                return
            raise e
        self.peers.set_app_data({unit_key: token})

    def remove_node_from_cluster(self, event: ClusterdRemoveNodeEvent) -> None:
        """Remove node from cluster."""
        if event.departing_unit is None:
            logger.debug("No unit to remove")
            return

        self_departing = event.departing_unit.name == self.unit.name
        already_left = self._wait_for_roles_to_settle_before_removal(
            event, self_departing
        )
        if already_left:
            return

        logger.debug(f"Departing unit: {event.departing_unit.name}")
        self._remove_member_from_cluster(event.departing_unit.name)
        if self.model.unit.is_leader():
            departing_key = f"{event.departing_unit.name}.join_token"
            self.peers.interface._app_data_bag.pop(
                departing_key,
                None,
            )
        if self_departing:
            self.status.set(ops.WaitingStatus("Waiting for removal"))
            member_left = self._wait_until_local_member_left_cluster()
            if member_left:
                return
            logger.warning(
                "Member %s has not left the cluster yet",
                event.departing_unit.name,
            )
            event.defer()
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for member to leave cluster"
            )
        self.status.set(ops.WaitingStatus("Waiting for roles to settle"))
        if not self._wait_until_roles_are_settled():
            logger.debug("Roles not settled yet")
            event.defer()
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for roles to settle"
            )

    def _wait_for_roles_to_settle_before_removal(
        self, event: ops.EventBase, self_departing: bool
    ) -> bool:
        """This method waits for rols to settle before removing a member.

        Returns true if the member has already left the cluster.
        """
        if self_departing:
            # We are the departing unit, and we might be the leader
            message = "Waiting for roles to settle before leaving cluster"
        else:
            # We are the leader, not the departing unit
            message = "Waiting for roles to settle before removing member"
        self.status.set(ops.WaitingStatus(message))
        # Leaving while the roles are not settled can cause the cluster to
        # be in an inconsistent state. So we wait until the roles are
        # settled before leaving.
        try:
            if not self._wait_until_roles_are_settled():
                logger.debug("Roles not settled yet")
                event.defer()
                raise sunbeam_guard.WaitingExceptionError(message)
        except requests.exceptions.HTTPError as e:
            if (
                e.response is not None
                and "Daemon not yet initialized" in e.response.text
            ):
                if self_departing:
                    logger.debug("Member already left cluster")
                    return True
        return False

    def _remove_member_from_cluster(self, departing_unit: str):
        """Helper method to remove a member from the cluster."""
        member_name = departing_unit.replace("/", "-")
        self_departing = departing_unit == self.unit.name
        try:
            logger.debug(f"Removing member {member_name}")
            self._clusterd.remove_node(
                member_name,
                force=True,
                allow_not_found=True,
            )
        except clusterd.ClusterdUnavailableError as e:
            if "Remote end closed connection without response" in str(e):
                logger.debug(
                    "Forwarded request failed, most likely because member was leader"
                    " and this member was removed."
                )
            else:
                raise e
        except requests.exceptions.HTTPError as e:
            if e.response is None:
                raise e
            is_503 = e.response.status_code == 503
            is_500 = e.response.status_code == 500
            if not self_departing or not (is_503 or is_500):
                raise e
            logger.debug(
                "Clusterd is not initialized, most likely because"
                " leader has already removed this unit from clusterd."
                " Error: %s",
                e.response.text,  # type: ignore
            )

    def join_node_to_cluster(self, event: ClusterdNodeAddedEvent) -> None:
        """Join node to cluster."""
        self.status.set(ops.MaintenanceStatus("Joining cluster"))
        token = self.peers.get_app_data(f"{self.unit.name}.join_token")
        if token is None:
            logger.warning("No token found for unit %s", self.unit.name)
            return
        member = self.unit.name.replace("/", "-")
        if not self.peers.interface.state.joined:
            self._clusterd.join(
                member,
                self._binding_address() + ":" + str(self.clusterd_port),
                token,
            )
            self.peers.interface.state.joined = True
            self.peers.set_unit_data({"joined": "true"})

        self.status.set(ops.WaitingStatus("Waiting for clusterd role"))
        is_role_set = self._wait_until_role_set(member)
        if not is_role_set:
            logger.debug("Member %s is still pending", member)
            event.defer()
            return

    def _wait_until_role_set(self, name: str) -> bool:
        @tenacity.retry(
            wait=tenacity.wait_fixed(5),
            stop=tenacity.stop_after_delay(300),
            retry=tenacity.retry_if_not_result(_identity),
        )
        def _wait_until_role_set(name: str) -> bool:
            member = self._clusterd.get_member(name)
            role = member.get("role")
            logger.debug(f"Member {name} role: {role}")
            if role == "PENDING":
                return False
            return True

        try:
            return _wait_until_role_set(name)
        except tenacity.RetryError:
            return False

    def _wait_until_roles_are_settled(self) -> bool:
        """Wait until cluster has odd number of voters."""

        @tenacity.retry(
            wait=tenacity.wait_fixed(5),
            stop=tenacity.stop_after_delay(60),
            retry=tenacity.retry_if_not_result(_identity),
        )
        def _wait_until_roles_are_settled() -> bool:
            members = self._clusterd.get_members()
            voter = 0
            for member in members:
                if member.get("role") == "voter":
                    voter += 1
            if voter % 2 == 0:
                return False
            return True

        try:
            return _wait_until_roles_are_settled()

        except tenacity.RetryError:
            return False

    def _wait_until_local_member_left_cluster(self) -> bool:
        """Wait until local node has left the cluster."""

        @tenacity.retry(
            wait=tenacity.wait_fixed(5),
            stop=tenacity.stop_after_delay(60),
            retry=tenacity.retry_if_not_result(_identity),
        )
        def _wait_until_local_member_left_cluster() -> bool:
            member_name = self.unit.name.replace("/", "-")
            try:
                self._clusterd.get_member(member_name)
                return False
            except requests.exceptions.HTTPError as e:
                if e.response is None:
                    raise e
                db_closed = "database is closed" in e.response.text
                clusterd_not_initialized = (
                    "Daemon not yet initialized" in e.response.text
                )
                if db_closed or clusterd_not_initialized:
                    logger.debug(
                        "Clusterd returned a known error while waiting for removal."
                        ". Skipping."
                        " Error: %s",
                        e.response.text,
                    )
                    return True
                raise e

        try:
            return _wait_until_local_member_left_cluster()
        except tenacity.RetryError:
            return False


if __name__ == "__main__":  # pragma: nocover
    ops.main(SunbeamClusterdCharm)
