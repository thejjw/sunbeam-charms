#!/usr/bin/env python3

#
# Copyright 2021 Canonical Ltd.
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
    List,
    Mapping,
    Optional,
)

import charms.cinder_ceph_k8s.v0.ceph_access as sunbeam_ceph_access  # noqa
import charms.cinder_k8s.v0.storage_backend as sunbeam_storage_backend  # noqa
import ops
import ops.charm
import ops_sunbeam.charm as charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.container_handlers as container_handlers
import ops_sunbeam.core as core
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
class CephConfigurationContext(config_contexts.ConfigContext):
    """Configuration context to parse ceph parameters."""

    def context(self) -> dict:
        """Generate configuration information for ceph config."""
        config = self.charm.model.config.get
        ctxt = {}
        if (
            config("pool-type")
            and config("pool-type") == sunbeam_rhandlers.ERASURE_CODED
        ):
            base_pool_name = config("rbd-pool") or config("rbd-pool-name")
            if not base_pool_name:
                base_pool_name = self.charm.app.name
            ctxt["rbd_default_data_pool"] = base_pool_name
        return ctxt


@sunbeam_tracing.trace_type
class CinderCephConfigurationContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

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
        # TODO:
        # secret_uuid needs to be generated and shared for the app
        return {
            "cluster_name": self.charm.app.name,
            "rbd_pool": pool_name,
            "rbd_user": self.charm.app.name,
            "backend_name": backend_name,
            "backend_availability_zone": config("backend-availability-zone"),
            "secret_uuid": self.charm.get_secret_uuid() or "unknown",
        }


@sunbeam_tracing.trace_type
class StorageBackendProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for storage-backend interface type."""

    def setup_event_handler(self):
        """Configure event handlers for an storage-backend relation."""
        logger.debug("Setting up Identity Service event handler")
        sb_svc = sunbeam_tracing.trace_type(
            sunbeam_storage_backend.StorageBackendProvides
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(sb_svc.on.api_ready, self._on_ready)
        return sb_svc

    def _on_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check whether storage-backend interface is ready for use."""
        return self.interface.remote_ready()


@sunbeam_tracing.trace_type
class CephAccessProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity service relation."""

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
class CinderVolumePebbleHandler(container_handlers.PebbleHandler):
    """Pebble handler for cinder-volume service."""

    def get_layer(self) -> dict:
        """cinder-volume service pebble layer.

        :returns: pebble layer configuration for cinder-volume service
        :rtype: dict
        """
        return {
            "summary": f"{self.service_name} layer",
            "description": "pebble config layer for cinder-volume service",
            "services": {
                self.service_name: {
                    "override": "replace",
                    "summary": self.service_name,
                    "command": f"{self.service_name} --use-syslog",
                    "user": "cinder",
                    "group": "cinder",
                },
            },
        }

    def start_service(self) -> None:
        """Start all services in associated container."""
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(
                f"{self.container_name} container is not ready. "
                "Cannot start service."
            )
            return
        service = container.get_service(self.service_name)
        if service.is_running():
            container.stop(self.service_name)

        container.start(self.service_name)

    def init_service(self, context) -> None:
        """Write configs and start services."""
        self.write_config(context)
        self.start_service()


@sunbeam_tracing.trace_sunbeam_charm
class CinderCephOperatorCharm(charm.OSBaseOperatorCharmK8S):
    """Cinder/Ceph Operator charm."""

    # NOTE: service_name == container_name
    service_name = "cinder-volume"

    service_user = "cinder"
    service_group = "cinder"

    cinder_conf = "/etc/cinder/cinder.conf"
    ceph_conf = "/etc/ceph/ceph.conf"

    client_secret_key = "secret-uuid"

    ceph_access_relation_name = "ceph-access"

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(api_ready=False)

    def configure_charm(self, event: ops.EventBase):
        """Catchall handler to configure charm services."""
        super().configure_charm(event)
        if self.has_ceph_relation() and self.ceph.ready:
            logger.info("CONFIG changed and ceph ready: calling request pools")
            self.ceph.request_pools(event)

    def get_relation_handlers(self) -> List[relation_handlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.ceph = relation_handlers.CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name="rbd",
            mandatory="ceph" in self.mandatory_relations,
        )
        handlers.append(self.ceph)
        self.sb_svc = StorageBackendProvidesHandler(
            self,
            "storage-backend",
            self.api_ready,
            "storage-backend" in self.mandatory_relations,
        )
        handlers.append(self.sb_svc)
        self.ceph_access = CephAccessProvidesHandler(
            self,
            "ceph-access",
            self.process_ceph_access_client_event,
        )
        handlers.append(self.ceph_access)
        return handlers

    def get_pebble_handlers(self) -> List[container_handlers.PebbleHandler]:
        """Pebble handlers for the operator."""
        return [
            CinderVolumePebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    def has_ceph_relation(self) -> bool:
        """Returns whether or not the application has been related to Ceph.

        :return: True if the ceph relation has been made, False otherwise.
        """
        return self.model.get_relation("ceph") is not None

    def api_ready(self, event) -> None:
        """Event handler for bootstrap of service when api services are ready."""
        self._state.api_ready = True
        self.configure_charm(event)
        if self.bootstrapped():
            for handler in self.pebble_handlers:
                handler.start_service()

    @property
    def config_contexts(self) -> List[config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(CephConfigurationContext(self, "ceph_config"))
        contexts.append(CinderCephConfigurationContext(self, "cinder_ceph"))
        return contexts

    @property
    def container_configs(self) -> List[core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                core.ContainerConfigFile(
                    self.cinder_conf,
                    "root",
                    self.service_group,
                    0o640,
                ),
                core.ContainerConfigFile(
                    self.ceph_conf,
                    "root",
                    self.service_group,
                    0o640,
                ),
            ]
        )
        return _cconfigs

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for cinder services."""
        return {"database": "cinder"}

    def configure_containers(self):
        """Setp ceph keyring and configure container that are ready."""
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                # The code for managing ceph client config should move to
                # a shared lib as it is common across clients.
                ph.execute(
                    [
                        "ceph-authtool",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "--create-keyring",
                        f"--name=client.{self.app.name}",
                        f"--add-key={self.ceph.key}",
                    ],
                    exception_on_error=True,
                )
                ph.execute(
                    [
                        "chown",
                        f"root:{self.service_group}",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "/etc/ceph/rbdmap",
                    ],
                    exception_on_error=True,
                )
                ph.execute(
                    [
                        "chmod",
                        "640",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "/etc/ceph/rbdmap",
                    ],
                    exception_on_error=True,
                )
                ph.configure_container(self.contexts())
            else:
                logging.debug(
                    f"Not running init for {ph.service_name},"
                    " container not ready"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Payload container not ready"
                )
        super().configure_containers()

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

    def get_secret_uuid(self) -> Optional[str]:
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
                "Cannot service client requests. "
                "Bootstrapped: {} Leader {}".format(
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
        self, relation_id: str = None
    ) -> bool:
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
    ops.main(CinderCephOperatorCharm)
