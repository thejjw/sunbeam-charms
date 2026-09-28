#!/usr/bin/env python3

#
# Copyright 2025 Canonical Ltd.
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

"""Cinder Ceph Operator Charm.

This charm provide Cinder <-> Ceph integration as part
of an OpenStack deployment
"""

import dataclasses
import enum
import json
import logging
import subprocess
import uuid
from typing import (
    Callable,
    Mapping,
)

import charms.cinder_volume_ceph.v0.ceph_access as sunbeam_ceph_access  # noqa
import ops
import ops.charm
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops_sunbeam import (
    compound_status,
)
from ops.model import (
    BlockedStatus,
    Relation,
    SecretRotate,
)

CEPH_CHECK_TIMEOUT = 60
FSID_PEER_KEY = "ceph-fsid"

logger = logging.getLogger(__name__)

class CephCheckResult(enum.IntEnum):
    """Exit code of `<snap>.ceph_check <backend>` (mirrored in snap-cinder-volume)"""

    OK = 0 
    ERROR = 1
    USAGE = 2
    POOL_MISSING = 3 
    PERMISSION_DENIED = 4 
    UNREACHABLE = 5

    @classmethod
    def _missing_(cls, value):
        return cls.ERROR


@dataclasses.dataclass(frozen=True)
class CephCheckOutcome:
    """Result of `<snap>.ceph-check backend."""

    rc: CephCheckResult
    fsid: str | None = None


@sunbeam_tracing.trace_type
class CinderCephConfigurationContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

    charm: "CinderVolumeCephOperatorCharm"

    def context(self) -> dict:
        """Generate context information for cinder config."""
        config = self.charm.model.config.get
        data_pool_name = config("rbd-pool-name") or self.charm.app.name
        if config("pool-type") == sunbeam_rhandlers.ERASURE_CODED:
            pool_name = (
                config("ec-rbd-metadata-pool") or f"{data_pool_name}-metadata"
            )
        else:
            pool_name = data_pool_name
        backend_name = config("volume-backend-name") or self.charm.app.name
        return {
            "rbd_pool": pool_name,
            "rbd_user": self.charm.ceph.client_name or self.charm.app.name,
            "backend_name": backend_name,
            "backend_availability_zone": config("backend-availability-zone"),
            "secret_uuid": self.charm.get_secret_uuid() or "unknown",
        }


@sunbeam_tracing.trace_type
class CephAccessProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity service relation."""

    interface: sunbeam_ceph_access.CephAccessProvides

    def __init__(
        self,
        charm: charm.OSBaseOperatorCharm,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Ceph Access event handler")
        ceph_access_svc = sunbeam_tracing.trace_type(
            sunbeam_ceph_access.CephAccessProvides
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ceph_access_svc.on.ready_ceph_access_clients,
            self._on_ceph_access_ready,
        )
        return ceph_access_svc

    def _on_ceph_access_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete.
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


@sunbeam_tracing.trace_type
class CinderVolumeCephClientHandler(sunbeam_rhandlers.CephClientHandler):
    """Ceph-client handler that can consume and existing pool."""

    @property
    def create_pool(self) -> bool:
        """Whether pool creation should be requested from the provider."""
        return bool(self.model.config.get("create-pool", True))

    def request_pools(self, event: ops.framework.EventBase) -> None:
        """Request pools, or only a key when create-pool is false."""
        if self.create_pool:
            super().request_pools(event)
            return
        relations = self.model.relations[self.relation_name]
        if not relations:
            return
        rq = self.interface.new_request
        self.interface._stored.broker_req = rq.request
        for relation in relations:
            unit_data = relation.data[self.model.unit]
            if unit_data.get("broker_req") != rq.request:
                unit_data["broker_req"] = rq.request


    def _remote_unit_value(self, field: str) -> str | None:
        """Value of *field* from the first remote unit that published a key."""
        for relation in self.model.relations[self.relation_name]:
            for unit in relation.units:
                data = relation.data[unit]
                if data.get("key") and data.get(field):
                    return data[field]
        return None


    @property
    def client_name(self) -> str | None:
        """CephX client name published by provider, if any."""
        return self._remote_unit_value("client-name")


    @property
    def provider_fsid(self) -> str | None:
        """Cluster FSID published by provider, if any."""
        return self._remote_unit_value("fsid")


    @property
    def broker_error(self) -> str | None:
        """Provider's error for our current broker request, if it failed."""
        rsp_key = "broker-rsp-" + self.model.unit.name.replace("/", "-")
        for relation in self.model.relations[self.relation_name]:
            try:
                sent = json.loads(
                    relation.data[self.model.unit].get("broker_req") or "{}"
                )
            except ValueError:
                continue
            request_id = sent.get("request-id")
            if not request_id:
                continue
            for unit in relation.units:
                try:
                    rsp = json.loads(relation.data[unit].get(rsp_key) or "{}")
                except ValueError:
                    continue
                if rsp.get("request-id") == request_id and rsp.get("exit-code"):
                    return rsp.get("stderr") or f"exit-code {rsp["exit-code"]}"
        return None


    def set_status(self, status: compound_status.Status) -> None:
        """Report a rejected broker request instead of waiting."""
        error = self.broker_error
        if error:
            status.set(BlockedStatus(f"ceph provider rejected request: {error}"))
            return
        super().set_status(status)


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeCephOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Ceph Operator charm."""

    service_name = "cinder-volume-ceph"

    client_secret_key = "secret-uuid"

    ceph_access_relation_name = "ceph-access"

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.framework.observe(self.on.update_status, self.configure_charm)
        self.framework.observe(
            self.on["ceph"].relation_broken, self._on_ceph_relation_broken
        )


    def _on_ceph_relation_broken(self, event: ops.RelationBrokenEvent) -> None:
        """Forget the pinned cluster: the next relation may be a different cluster."""
        if self.unit.is_leader():
            self.peers.set_app_data({FSID_PEER_KEY: ""})
        self.configure_charm(event)

    def configure_charm(self, event: ops.EventBase):
        """Catchall handler to configure charm services."""
        super().configure_charm(event)
        if self.has_ceph_relation() and self.ceph.ready:
            logger.info("CONFIG changed and ceph ready: calling request pools")
            self.ceph.request_pools(event)

    @property
    def backend_key(self) -> str:
        """Return the backend key."""
        return "ceph." + self.model.app.name

    def get_relation_handlers(
        self, handlers: list[relation_handlers.RelationHandler] | None = None
    ) -> list[relation_handlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        self.ceph = CinderVolumeCephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name="rbd",
            mandatory="ceph" in self.mandatory_relations,
        )
        handlers.append(self.ceph)
        self.ceph_access = CephAccessProvidesHandler(
            self,
            "ceph-access",
            self.process_ceph_access_client_event,
        )  # type: ignore
        handlers.append(self.ceph_access)
        return super().get_relation_handlers(handlers)

    def has_ceph_relation(self) -> bool:
        """Returns whether or not the application has been related to Ceph.

        :return: True if the ceph relation has been made, False otherwise.
        """
        return self.model.get_relation("ceph") is not None

    def get_backend_configuration(self) -> Mapping:
        """Return the backend configuration."""
        try:
            contexts = self.contexts()
            return {
                "volume-backend-name": contexts.cinder_ceph.backend_name,
                "backend-availability-zone": contexts.cinder_ceph.backend_availability_zone,
                "mon-hosts": contexts.ceph.mon_hosts,
                "rbd-pool": contexts.cinder_ceph.rbd_pool,
                "rbd-user": contexts.cinder_ceph.rbd_user,
                "rbd-secret-uuid": contexts.cinder_ceph.secret_uuid,
                "rbd-key": contexts.ceph.key,
                "auth": contexts.ceph.auth,
                "fsid": self.peers.get_app_data(FSID_PEER_KEY) or
                self.ceph.provider_fsid,
            }
        except AttributeError as e:
            raise sunbeam_guard.WaitingExceptionError(
                "Data missing: {}".format(e.name)
            )

    @property
    def config_contexts(self) -> list[config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        return [CinderCephConfigurationContext(self, "cinder_ceph")]

    def _set_or_update_rbd_secret(
        self,
        ceph_key: str,
        scope: dict = {},
        rotate: SecretRotate = SecretRotate.NEVER,
    ) -> str:
        """Create ceph access secret or update it.

        Create ceph access secret or if it already exists check the contents
        and update them if needed.
        """
        rbd_secret_uuid_id = self.peers.get_app_data(self.client_secret_key)
        if rbd_secret_uuid_id:
            secret = self.model.get_secret(id=rbd_secret_uuid_id)
            secret_data = secret.get_content(refresh=True)
            if secret_data.get("key") != ceph_key:
                secret_data["key"] = ceph_key
                secret.set_content(secret_data)
        else:
            secret = self.model.app.add_secret(
                {
                    "uuid": str(uuid.uuid4()),
                    "key": ceph_key,
                },
                label=self.client_secret_key,
                rotate=rotate,
            )
            self.peers.set_app_data(
                {
                    self.client_secret_key: secret.id,
                }
            )
        if "relation" in scope:
            secret.grant(scope["relation"])

        return secret.id

    def get_secret_uuid(self) -> str | None:
        """Get the secret uuid."""
        uuid = None
        rbd_secret_uuid_id = self.peers.get_app_data(self.client_secret_key)
        if rbd_secret_uuid_id:
            secret = self.model.get_secret(id=rbd_secret_uuid_id)
            secret_data = secret.get_content(refresh=True)
            uuid = secret_data["uuid"]
        return uuid

    def configure_app_leader(self, event: ops.framework.EventBase):
        """Run global app setup.

        These are tasks that should only be run once per application and only
        the leader runs them.
        """
        if self.ceph.ready:
            self._set_or_update_rbd_secret(self.ceph.key)
            self.set_leader_ready()
            self.broadcast_ceph_access_credentials()
        else:
            raise sunbeam_guard.WaitingExceptionError(
                "Ceph relation not ready"
            )

    def can_service_requests(self) -> bool:
        """Check if unit can process client requests."""
        if self.bootstrapped() and self.unit.is_leader():
            logger.debug("Can service client requests")
            return True
        else:
            logger.debug(
                "Cannot service client requests. Bootstrapped: {} Leader {}".format(
                    self.bootstrapped(), self.unit.is_leader()
                )
            )
            return False

    def send_ceph_access_credentials(self, relation: Relation):
        """Send clients a link to the secret and grant them access."""
        rbd_secret_uuid_id = self.peers.get_app_data(self.client_secret_key)
        secret = self.model.get_secret(id=rbd_secret_uuid_id)
        secret.grant(relation)
        self.ceph_access.interface.set_ceph_access_credentials(
            self.ceph_access_relation_name, relation.id, rbd_secret_uuid_id
        )

    def process_ceph_access_client_event(self, event: ops.framework.EventBase):
        """Inform a single client of the access data."""
        self.broadcast_ceph_access_credentials(relation_id=event.relation.id)

    def broadcast_ceph_access_credentials(
        self, relation_id: str | None = None
    ) -> None:
        """Send ceph access data to clients."""
        logger.debug("Checking for outstanding client requests")
        if not self.can_service_requests():
            return
        for relation in self.framework.model.relations[
            self.ceph_access_relation_name
        ]:
            if relation_id and relation.id == relation_id:
                self.send_ceph_access_credentials(relation)
            elif not relation_id:
                self.send_ceph_access_credentials(relation)

    def configure_snap(self, event: ops.EventBase) -> None:
        """Configure the backend, then verify its pool before going ready."""
        if not bool(self._state.volume_ready):
            raise sunbeam_guard.WaitingExceptionError("Volume not ready")
        backend_context = self.get_backend_configuration()
        self.set_snap_data(backend_context, namespace=self.backend_key)
        fsid = self.check_pool(backend_context)
        if fsid:
            self.check_fsid(fsid)
            if backend_context.get("fsid") != fsid:
                self.set_snap_data({"fsid": fsid}, namespace=self.backend_key)
        self.cinder_volume.interface.set_ready()

    def check_pool(self, backend_context: Mapping) -> str | None:
        """Verify the pool using the backend's own credentials."""
        outcome = self._run_ceph_check()
        if outcome is None:
            return None
        pool = backend_context["rbd-pool"]
        user = backend_context["rbd-user"]
        match outcome.rc:
            case CephCheckResult.OK:
                return outcome.fsid
            case CephCheckResult.POOL_MISSING:
                msg = f"pool '{pool}' does not exist"
            case CephCheckResult.PERMISSION_DENIED:
                msg = f"client.{user} not authorized for pool '{pool}'"
            case CephCheckResult.UNREACHABLE:
                msg = f"ceph cluster unreachable"
            case CephCheckResult.ERROR | CephCheckResult.USAGE:
                msg = f"ceph pool check failed; see juju debug-log"
        raise sunbeam_guard.BlockedExceptionError(msg)


    def check_fsid(self, fsid: str) -> None:
        """Ensure this backend remains paired with the same cluster."""
        provider = self.ceph.provider_fsid
        if provider and provider != fsid:
            raise sunbeam_guard.BlockedExceptionError(
                f"ceph fsid mismatch (provider {provider}, cluster {fsid})"
            )
        pinned = self.peers.get_app_data(FSID_PEER_KEY)
        if pinned and pinned != fsid:
            raise sunbeam_guard.BlockedExceptionError(
                f"ceph cluster fsid changed (expected {pinned}, got {fsid})"
            )
        if not pinned and self.unit.is_leader():
            self.peers.set_app_data({FSID_PEER_KEY: fsid})


    def _run_ceph_check(self) -> CephCheckOutcome | None:
        """Run `<snap>.ceph-check <backend>; None if the snap lacks it"""
        snap_svc = self.get_snap()
        if not any(app.get("name") == "ceph-check" for app in snap_svc.apps):
            logger.warning(f"{self.snap_name} has no ceph-check command; skipping pool verification")
            return None
        cmd = [
            "snap",
            "run"
            f"{self.snap_name}.ceph-check",
            self.model.app.name
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output = True,
                text=True,
                timeout=CEPH_CHECK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ceph-check timed out")
            return CephCheckOutcome(CephCheckResult.UNREACHABLE)
        if result.returncode != CephCheckResult.OK:
            logger.warning(f"ceph-check rc={result.returncode}")
            return CephCheckOutcome(CephCheckResult(result.returncode))
        try:
            fsid = json.loads(result.stdout or "{}").get("fsid")
        except (ValueError, AttributeError):
            logger.warning(f"ceph-check printed no usable fsid: {result.stdout}")
            fsid = None
        return CephCheckOutcome(CephCheckResult.OK, fsid)

if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeCephOperatorCharm)
