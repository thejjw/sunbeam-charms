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

import logging
import uuid
from typing import (
    Callable,
    Mapping,
)

import charms.cinder_ceph_k8s.v0.ceph_access as sunbeam_ceph_access  # noqa
import ops
import ops.charm
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
from ops.model import (
    Relation,
    SecretRotate,
)

logger = logging.getLogger(__name__)


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
            "rbd_user": self.charm.app.name,
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


@sunbeam_tracing.trace_sunbeam_charm
class CinderVolumeCephOperatorCharm(charm.OSCinderVolumeDriverOperatorCharm):
    """Cinder/Ceph Operator charm."""

    service_name = "cinder-volume-ceph"

    client_secret_key = "secret-uuid"

    ceph_access_relation_name = "ceph-access"

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
        self.ceph = relation_handlers.CephClientHandler(
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


if __name__ == "__main__":  # pragma: nocover
    ops.main(CinderVolumeCephOperatorCharm)
