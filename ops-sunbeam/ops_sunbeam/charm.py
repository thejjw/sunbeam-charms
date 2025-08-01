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

"""Base classes for defining a charm using the Operator framework.

This library provided OSBaseOperatorCharm and OSBaseOperatorAPICharm. The
charm classes use ops_sunbeam.relation_handlers.RelationHandler objects
to interact with relations. These objects also provide contexts which
can be used when defining templates.

In addition to the Relation handlers the charm class can also use
ops_sunbeam.config_contexts.ConfigContext objects which can be
used when rendering templates, these are not specific to a relation.

The charm class interacts with the containers it is managing via
ops_sunbeam.container_handlers.PebbleHandler. The PebbleHandler
defines the pebble layers, manages pushing configuration to the
containers and managing the service running in the container.
"""

import functools
import ipaddress
import logging
import urllib
import urllib.parse
from typing import (
    TYPE_CHECKING,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
)

import ops.charm
import ops.framework
import ops.model
import ops.pebble
import ops.storage
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import tenacity
from ops.charm import (
    SecretChangedEvent,
    SecretRemoveEvent,
    SecretRotateEvent,
)
from ops.model import (
    ActiveStatus,
    MaintenanceStatus,
)

if TYPE_CHECKING:
    import charms.operator_libs_linux.v2.snap as snap

logger = logging.getLogger(__name__)


class OSBaseOperatorCharm(
    ops.charm.CharmBase, metaclass=sunbeam_core.PostInitMeta
):
    """Base charms for OpenStack operators."""

    _state = ops.framework.StoredState()

    # Holds set of mandatory relations
    # Auto-updates the mandatory requires relations from charmcraft.yaml
    mandatory_relations: set[str] = set()
    service_name: str

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        if isinstance(self.framework._storage, ops.storage.JujuStorage):
            raise ValueError(
                (
                    "use_juju_for_storage=True is deprecated and not supported "
                    "by ops_sunbeam"
                )
            )

        # Update mandatory relations from charmcraft.yaml definitions
        requires_relations: set[str] = {
            name
            for name, metadata in self.meta.requires.items()
            if metadata.optional is False
        }
        self.mandatory_relations = requires_relations.union(
            self.mandatory_relations
        )

        # unit_bootstrapped is stored in the local unit storage which is lost
        # when the pod is replaced, so this will revert to False on charm
        # upgrade or upgrade of the payload container.
        self._state.set_default(unit_bootstrapped=False)
        self.status = compound_status.Status("workload", priority=100)
        self.status_pool = compound_status.StatusPool(self)
        self.status_pool.add(self.status)
        self.bootstrap_status = compound_status.Status(
            "bootstrap", priority=90
        )
        self.status_pool.add(self.bootstrap_status)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)
        self.framework.observe(self.on.secret_rotate, self._on_secret_rotate)
        self.framework.observe(self.on.secret_remove, self._on_secret_remove)
        self.framework.observe(
            self.on.collect_unit_status, self._on_collect_unit_status_event
        )

    def __post_init__(self):
        """Post init hook."""
        self.relation_handlers = self.get_relation_handlers()
        if not self.bootstrapped():
            self.bootstrap_status.set(
                MaintenanceStatus("Service not bootstrapped")
            )

    def can_add_handler(
        self,
        relation_name: str,
        handlers: List[sunbeam_rhandlers.RelationHandler],
    ) -> bool:
        """Whether a handler for the given relation can be added."""
        if relation_name not in self.meta.relations.keys():
            logging.debug(
                f"Cannot add handler for relation {relation_name}, relation "
                "not present in charm metadata"
            )
            return False
        if relation_name in [h.relation_name for h in handlers]:
            logging.debug(
                f"Cannot add handler for relation {relation_name}, handler "
                "already present"
            )
            return False
        return True

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("tracing", handlers):
            self.tracing = sunbeam_rhandlers.TracingRequireHandler(
                self, "tracing", "tracing" in self.mandatory_relations
            )
        if self.can_add_handler("amqp", handlers):
            self.amqp = sunbeam_rhandlers.RabbitMQHandler(
                self,
                "amqp",
                self.configure_charm,
                str(self.config.get("rabbit-user") or self.service_name),
                str(self.config.get("rabbit-vhost") or "openstack"),
                self.remote_external_access,
                "amqp" in self.mandatory_relations,
            )
            handlers.append(self.amqp)
        self.dbs = {}
        for relation_name, database_name in self.databases.items():
            if self.can_add_handler(relation_name, handlers):
                db = sunbeam_rhandlers.DBHandler(
                    self,
                    relation_name,
                    self.configure_charm,
                    database_name,
                    relation_name in self.mandatory_relations,
                    external_access=self.remote_external_access,
                )
                self.dbs[relation_name] = db
                handlers.append(db)
        if self.can_add_handler("peers", handlers):
            self.peers = sunbeam_rhandlers.BasePeerHandler(
                self, "peers", self.configure_charm, False
            )
            handlers.append(self.peers)
        if self.can_add_handler("certificates", handlers):
            self.certs = sunbeam_rhandlers.TlsCertificatesHandler(
                self,
                "certificates",
                self.configure_charm,
                sans_dns=self.get_sans_dns(),
                sans_ips=self.get_sans_ips(),
                mandatory="certificates" in self.mandatory_relations,
            )
            handlers.append(self.certs)
        if self.can_add_handler("identity-credentials", handlers):
            self.ccreds = sunbeam_rhandlers.IdentityCredentialsRequiresHandler(
                self,
                "identity-credentials",
                self.configure_charm,
                "identity-credentials" in self.mandatory_relations,
            )
            handlers.append(self.ccreds)
        if self.can_add_handler("ceph-access", handlers):
            self.ceph_access = sunbeam_rhandlers.CephAccessRequiresHandler(
                self,
                "ceph-access",
                self.configure_charm,
                "ceph-access" in self.mandatory_relations,
            )
            handlers.append(self.ceph_access)
        if self.can_add_handler("receive-ca-cert", handlers):
            self.receive_ca_cert = (
                sunbeam_rhandlers.CertificateTransferRequiresHandler(
                    self, "receive-ca-cert", self.configure_charm
                )
            )
            handlers.append(self.receive_ca_cert)

        return handlers

    @property
    def remote_external_access(self) -> bool:
        """Whether this charm needs external access for remote service.

        If the service needs special handling for remote access, this function
        should be overridden to return True.

        Example, remote service needs to expose a LoadBalancer service.
        """
        return True

    def get_tracing_endpoint(self) -> str | None:
        """Get the tracing endpoint for the service."""
        if hasattr(self, "tracing"):
            return self.tracing.tracing_endpoint()
        return None

    def get_sans_ips(self) -> List[str]:
        """Return Subject Alternate Names to use in cert for service."""
        str_ips_sans = [str(s) for s in self._ip_sans()]
        return list(set(str_ips_sans))

    def get_sans_dns(self) -> List[str]:
        """Return Subject Alternate Names to use in cert for service."""
        return list(set(self.get_domain_name_sans()))

    def _get_all_relation_addresses(self) -> list[ipaddress.IPv4Address]:
        """Return all bind/ingress addresses from all relations."""
        addresses = []
        for relation_name in self.meta.relations.keys():
            for relation in self.framework.model.relations.get(
                relation_name, []
            ):
                binding = self.model.get_binding(relation)
                if binding is None or binding.network is None:
                    continue
                if isinstance(
                    binding.network.ingress_address, ipaddress.IPv4Address
                ):
                    addresses.append(binding.network.ingress_address)
                if isinstance(
                    binding.network.bind_address, ipaddress.IPv4Address
                ):
                    addresses.append(binding.network.bind_address)
        return addresses

    def _ip_sans(self) -> list[ipaddress.IPv4Address]:
        """Get IPv4 addresses for service."""
        ip_sans = self._get_all_relation_addresses()

        for binding_name in ["public"]:
            try:
                binding = self.model.get_binding(binding_name)
                if binding is None or binding.network is None:
                    continue
                if isinstance(
                    binding.network.ingress_address, ipaddress.IPv4Address
                ):
                    ip_sans.append(binding.network.ingress_address)
                if isinstance(
                    binding.network.bind_address, ipaddress.IPv4Address
                ):
                    ip_sans.append(binding.network.bind_address)
            except ops.model.ModelError:
                logging.debug(f"No binding found for {binding_name}")
        return ip_sans

    def get_domain_name_sans(self) -> List[str]:
        """Get Domain names for service."""
        return []

    def check_leader_ready(self):
        """Check the leader is reporting as ready."""
        if self.supports_peer_relation and not (
            self.unit.is_leader() or self.is_leader_ready()
        ):
            raise sunbeam_guard.WaitingExceptionError("Leader not ready")

    def check_relation_handlers_ready(self, event: ops.framework.EventBase):
        """Check all relation handlers are ready."""
        not_ready_relations = self.get_mandatory_relations_not_ready(event)
        if not_ready_relations:
            logger.info(f"Relations {not_ready_relations} incomplete")
            self.stop_services(not_ready_relations)
            raise sunbeam_guard.WaitingExceptionError(
                "Not all relations are ready"
            )

    def update_relations(self):
        """Update relation data."""
        for handler in self.relation_handlers:
            try:
                handler.update_relation_data()
            except NotImplementedError:
                logging.debug(f"send_requests not implemented for {handler}")

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self._state.unit_bootstrapped = True

    def configure_app_leader(self, event):
        """Run global app setup.

        These are tasks that should only be run once per application and only
        the leader runs them.
        """
        self.set_leader_ready()

    def configure_app_non_leader(self, event):
        """Setup steps for a non-leader after leader has bootstrapped."""
        if not self.bootstrapped():
            raise sunbeam_guard.WaitingExceptionError("Leader not ready")

    def configure_app(self, event):
        """Check on (and run if leader) app wide tasks."""
        if self.unit.is_leader():
            self.configure_app_leader(event)
        else:
            self.configure_app_non_leader(event)

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        logger.info("Setting active status")
        self.status.set(ActiveStatus(""))

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Catchall handler to configure charm services."""
        with sunbeam_guard.guard(self, "Bootstrapping"):
            # Publishing relation data may be dependent on something else (like
            # receiving a piece of data from the leader). To cover that
            # republish relation if the relation adapter has implemented an
            # update method.
            self.update_relations()
            self.configure_unit(event)
            self.configure_app(event)
            self.bootstrap_status.set(ActiveStatus())
            self.post_config_setup()

    def stop_services(self, relation: Optional[Set[str]] = None) -> None:
        """Stop all running services."""
        # Machine charms should implement this function if required.

    @property
    def supports_peer_relation(self) -> bool:
        """Whether the charm support the peers relation."""
        return "peers" in self.meta.relations.keys()

    @property
    def config_contexts(
        self,
    ) -> list[sunbeam_config_contexts.ConfigContext]:
        """Return the configuration adapters for the operator."""
        return [sunbeam_config_contexts.CharmConfigContext(self, "options")]

    @property
    def _unused_handler_prefix(self) -> str:
        """Prefix for handlers."""
        return self.service_name.replace("-", "_")

    @property
    def template_dir(self) -> str:
        """Directory containing Jinja2 templates."""
        return "src/templates"

    @property
    def databases(self) -> Mapping[str, str]:
        """Return a mapping of database relation names to database names.

        Use this to define the databases required by an application.

        All entries here
        that have a corresponding relation defined in metadata
        will automatically have a a DBHandler instance set up for it,
        and assigned to `charm.dbs[relation_name]`.
        Entries that don't have a matching relation in metadata
        will be ignored.
        Note that the relation interface type is expected to be 'mysql_client'.

        It defaults to loading a relation named "database",
        with the database named after the service name.
        """
        return {"database": self.service_name.replace("-", "_")}

    def _on_collect_unit_status_event(self, event: ops.CollectStatusEvent):
        """Publish the unit status.

        collect_unit_status is called at the end of the hook's execution,
        making it the best place to publish an active status.
        """
        status = self.status_pool.compute_status()
        if status:
            event.add_status(status)

    def _on_config_changed(self, event: ops.framework.EventBase) -> None:
        self.configure_charm(event)

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        # By default read the latest content of secret
        # this will allow juju to trigger secret-remove
        # event for old revision
        event.secret.get_content(refresh=True)
        self.configure_charm(event)

    def _on_secret_rotate(self, event: SecretRotateEvent) -> None:
        # Placeholder to handle secret rotate event
        # charms should handle the event if required
        pass

    def _on_secret_remove(self, event: SecretRemoveEvent) -> None:
        # Placeholder to handle secret remove event
        # charms should handle the event if required
        pass

    def check_broken_relations(
        self, relations: set, event: ops.framework.EventBase
    ) -> set:
        """Return all broken relations on given set of relations."""
        broken_relations = set()

        # Check for each relation if the event is gone away event.
        # lazy import the events
        # Note: Ceph relation not handled as there is no gone away event.
        for relation in relations:
            _is_broken = False
            match relation:
                case "database" | "api-database" | "cell-database":
                    from ops.charm import (
                        RelationBrokenEvent,
                    )

                    if isinstance(event, RelationBrokenEvent):
                        _is_broken = event.relation.name in (
                            "database",
                            "api-database",
                            "cell-database",
                        )
                case "ingress-public" | "ingress-internal":
                    from charms.traefik_k8s.v2.ingress import (
                        IngressPerAppRevokedEvent,
                    )

                    if isinstance(event, IngressPerAppRevokedEvent):
                        _is_broken = True
                case "identity-service":
                    from charms.keystone_k8s.v1.identity_service import (
                        IdentityServiceGoneAwayEvent,
                    )

                    if isinstance(event, IdentityServiceGoneAwayEvent):
                        _is_broken = True
                case "amqp":
                    from charms.rabbitmq_k8s.v0.rabbitmq import (
                        RabbitMQGoneAwayEvent,
                    )

                    if isinstance(event, RabbitMQGoneAwayEvent):
                        _is_broken = True
                case "certificates":
                    from charms.tls_certificates_interface.v3.tls_certificates import (
                        AllCertificatesInvalidatedEvent,
                    )

                    if isinstance(event, AllCertificatesInvalidatedEvent):
                        _is_broken = True
                case "ovsdb-cms":
                    from charms.ovn_central_k8s.v0.ovsdb import (
                        OVSDBCMSGoneAwayEvent,
                    )

                    if isinstance(event, OVSDBCMSGoneAwayEvent):
                        _is_broken = True
                case "identity-credentials":
                    from charms.keystone_k8s.v0.identity_credentials import (
                        IdentityCredentialsGoneAwayEvent,
                    )

                    if isinstance(event, IdentityCredentialsGoneAwayEvent):
                        _is_broken = True
                case "identity-ops":
                    from charms.keystone_k8s.v0.identity_resource import (
                        IdentityOpsProviderGoneAwayEvent,
                    )

                    if isinstance(event, IdentityOpsProviderGoneAwayEvent):
                        _is_broken = True
                case "gnocchi-db":
                    from charms.gnocchi_k8s.v0.gnocchi_service import (
                        GnocchiServiceGoneAwayEvent,
                    )

                    if isinstance(event, GnocchiServiceGoneAwayEvent):
                        _is_broken = True
                case "ceph-access":
                    from charms.cinder_ceph_k8s.v0.ceph_access import (
                        CephAccessGoneAwayEvent,
                    )

                    if isinstance(event, CephAccessGoneAwayEvent):
                        _is_broken = True
                case "dns-backend":
                    from charms.designate_bind_k8s.v0.bind_rndc import (
                        BindRndcGoneAwayEvent,
                    )

                    if isinstance(event, BindRndcGoneAwayEvent):
                        _is_broken = True

            if _is_broken:
                broken_relations.add(relation)

        return broken_relations

    def get_mandatory_relations_not_ready(
        self, event: ops.framework.EventBase
    ) -> Set[str]:
        """Get mandatory relations that are not ready for use."""
        ready_relations = {
            handler.relation_name
            for handler in self.relation_handlers
            if handler.mandatory and handler.ready
        }

        # The relation data for broken relations are not cleared during
        # processing of gone away event. This is a temporary workaround
        # to mark broken relations as not ready.
        # The workaround can be removed once the below bug is resolved
        # https://bugs.launchpad.net/juju/+bug/2024583
        # https://github.com/canonical/operator/issues/940
        broken_relations = self.check_broken_relations(ready_relations, event)
        ready_relations = ready_relations.difference(broken_relations)

        not_ready_relations = self.mandatory_relations.difference(
            ready_relations
        )

        return not_ready_relations

    def contexts(self) -> sunbeam_core.OPSCharmContexts:
        """Construct context for rendering templates."""
        ra = sunbeam_core.OPSCharmContexts(self)
        for handler in self.relation_handlers:
            if handler.relation_name not in self.meta.relations.keys():
                logger.info(
                    f"Dropping handler for relation {handler.relation_name}, "
                    "relation not present in charm metadata"
                )
                continue
            if handler.ready:
                ra.add_relation_handler(handler)
        ra.add_config_contexts(self.config_contexts)
        return ra

    def bootstrapped(self) -> bool:
        """Determine whether the service has been bootstrapped."""
        return (
            self._state.unit_bootstrapped  # type: ignore[truthy-function] # unit_bootstrapped is not a function
            and self.is_leader_ready()
        )

    def leader_set(
        self,
        settings: sunbeam_core.RelationDataMapping | None = None,
        **kwargs,
    ) -> None:
        """Juju set data in peer data bag."""
        settings = settings or {}
        settings.update(kwargs)
        self.peers.set_app_data(settings=settings)

    def leader_get(self, key: str) -> str | None:
        """Retrieve data from the peer relation."""
        return self.peers.get_app_data(key)

    def set_leader_ready(self) -> None:
        """Tell peers that the leader is ready."""
        try:
            self.peers.set_leader_ready()
        except AttributeError:
            logging.warning("Cannot set leader ready as peer relation missing")

    def is_leader_ready(self) -> bool:
        """Has the lead unit announced that it is ready."""
        leader_ready = False
        try:
            leader_ready = self.peers.is_leader_ready()
        except AttributeError:
            logging.warning(
                "Cannot check leader ready as peer relation missing. "
                "Assuming it is ready."
            )
            leader_ready = True
        return leader_ready


class OSBaseOperatorCharmK8S(OSBaseOperatorCharm):
    """Base charm class for k8s based charms."""

    db_sync_timeout = 300

    def __post_init__(self):
        """Post init hook."""
        super().__post_init__()
        self.pebble_handlers = self.get_pebble_handlers()

    @property
    def service_dns(self) -> str:
        """Dns name for the service."""
        return f"{self.app.name}.{self.model.name}.svc"

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the operator."""
        return [
            sunbeam_chandlers.PebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    def get_named_pebble_handler(
        self, container_name: str
    ) -> sunbeam_chandlers.PebbleHandler | None:
        """Get pebble handler matching container_name."""
        pebble_handlers = [
            h
            for h in self.pebble_handlers
            if h.container_name == container_name
        ]
        assert (
            len(pebble_handlers) < 2
        ), "Multiple pebble handlers with the same name found."
        if pebble_handlers:
            return pebble_handlers[0]
        else:
            return None

    def get_named_pebble_handlers(
        self, container_names: Sequence[str]
    ) -> list[sunbeam_chandlers.PebbleHandler]:
        """Get pebble handlers matching container_names."""
        return [
            h
            for h in self.pebble_handlers
            if h.container_name in container_names
        ]

    @property
    def remote_external_access(self) -> bool:
        """Whether this charm needs external access for remote service.

        Most often, k8s services don't need a special access to communicate.
        """
        return False

    def configure_containers(self):
        """Configure containers."""
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                ph.configure_container(self.contexts())
            else:
                logging.debug(
                    f"Not configuring {ph.service_name}, container not ready"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Payload container not ready"
                )

    def init_container_services(self):
        """Run init on pebble handlers that are ready."""
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                logging.debug(f"Running init for {ph.service_name}")
                ph.init_service(self.contexts())
            else:
                logging.debug(
                    f"Not running init for {ph.service_name},"
                    " container not ready"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Payload container not ready"
                )

    def check_pebble_handlers_ready(self):
        """Check pebble handlers are ready."""
        for ph in self.pebble_handlers:
            if not ph.service_ready:
                logging.debug(
                    f"Aborting container {ph.service_name} service not ready"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Container service not ready"
                )

    def stop_services(self, relation: set[str] | None = None) -> None:
        """Stop all running services."""
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                logging.debug(
                    f"Stopping all services in container {ph.container_name}"
                )
                ph.stop_all()

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.open_ports()
        self.configure_containers()
        self.run_db_sync()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        self._state.unit_bootstrapped = True

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("logging", handlers):
            self.logging = sunbeam_rhandlers.LogForwardHandler(
                self,
                "logging",
                "logging" in self.mandatory_relations,
            )
            handlers.append(self.logging)
        return super().get_relation_handlers(handlers)

    def add_pebble_health_checks(self):
        """Add health checks for services in payload containers."""
        for ph in self.pebble_handlers:
            ph.add_healthchecks()

    def post_config_setup(self):
        """Configuration steps after services have been setup."""
        self.add_pebble_health_checks()
        logger.info("Setting active status")
        self.status.set(ActiveStatus(""))

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the operator."""
        return []

    @property
    def container_names(self) -> list[str]:
        """Names of Containers that form part of this service."""
        return [self.service_name]

    def containers_ready(self) -> bool:
        """Determine whether all containers are ready for configuration."""
        for ph in self.pebble_handlers:
            if not ph.service_ready:
                logger.info(f"Container incomplete: {ph.container_name}")
                return False
        return True

    @property
    def db_sync_container_name(self) -> str:
        """Name of Containerto run db sync from."""
        return self.service_name

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(10),
        retry=(
            tenacity.retry_if_exception_type(ops.pebble.ChangeError)
            | tenacity.retry_if_exception_type(ops.pebble.ExecError)
            | tenacity.retry_if_exception_type(ops.pebble.TimeoutError)
        ),
        after=tenacity.after_log(logger, logging.WARNING),
        wait=tenacity.wait_exponential(multiplier=1, min=10, max=300),
    )
    def _retry_db_sync(self, cmd):
        container = self.unit.get_container(self.db_sync_container_name)
        logging.debug("Running sync: \n%s", cmd)
        try:
            process = container.exec(cmd, timeout=self.db_sync_timeout)
            out, err = process.wait_output()
        except ops.pebble.TimeoutError as e:
            logger.warning(f"DB Sync command timed out: {e}")
            raise e
        except ops.pebble.ChangeError as e:
            logger.warning(f"Failed to run DB Sync command: {e}")
            raise e
        except ops.pebble.ExecError as e:
            logger.warning(f"DB Sync stderr: {str(e.stderr)}")
            logger.warning(f"DB Sync stdout: {str(e.stdout)}")
            raise e
        if err:
            for line in err.splitlines():
                logger.warning("DB Sync stderr: %s", line.strip())
        if out:
            for line in out.splitlines():
                logger.debug("DB Sync stdout: %s", line.strip())

    @sunbeam_job_ctrl.run_once_per_unit("db-sync")
    def run_db_sync(self) -> None:
        """Run DB sync to init DB.

        :raises: pebble.ExecError
        """
        if not self.unit.is_leader():
            logging.info("Not lead unit, skipping DB syncs")
            return

        if db_sync_cmds := getattr(self, "db_sync_cmds", None):
            if db_sync_cmds:
                logger.info("Syncing database...")
                for cmd in db_sync_cmds:
                    try:
                        self._retry_db_sync(cmd)
                    except tenacity.RetryError:
                        raise sunbeam_guard.BlockedExceptionError(
                            "DB sync failed"
                        )
        else:
            logger.warning(
                "Not DB sync ran. Charm does not specify self.db_sync_cmds"
            )

    def open_ports(self):
        """Register ports in underlying cloud."""
        pass


class OSBaseOperatorAPICharm(OSBaseOperatorCharmK8S):
    """Base class for OpenStack API operators."""

    wsgi_admin_script: str
    wsgi_public_script: str

    @property
    def service_endpoints(self) -> list[dict]:
        """List of endpoints for this service."""
        return []

    @property
    def ingress_healthcheck_path(self):
        """Default ingress healthcheck path.

        This value can be overridden at the charm level as shown in
        keystone-k8s/src/charm.py.
        """
        return "/"

    @property
    def ingress_healthcheck_interval(self):
        """Default ingress healthcheck interval.

        This value can be overridden at the charm level. Time values
        following Golang time.ParseDuration() format are valid.
        """
        return "30s"

    @property
    def ingress_healthcheck_timeout(self):
        """Default ingress healthcheck timeout.

        This value can be overridden at the charm level. Time values
        following Golang time.ParseDuration() format are valid.
        """
        return "5s"

    @property
    def ingress_healthcheck_params(self):
        """Dictionary of ingress healthcheck values."""
        params = {
            "path": self.ingress_healthcheck_path,
            "interval": self.ingress_healthcheck_interval,
            "timeout": self.ingress_healthcheck_timeout,
        }

        return params

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        # Note: intentionally including the ingress handler here in order to
        # be able to link the ingress and identity-service handlers.
        if self.can_add_handler("ingress-internal", handlers):
            self.ingress_internal = sunbeam_rhandlers.IngressInternalHandler(
                self,
                "ingress-internal",
                self.service_name,
                self.default_public_ingress_port,
                self.ingress_healthcheck_params,
                self._ingress_changed,
                "ingress-internal" in self.mandatory_relations,
            )
            handlers.append(self.ingress_internal)
        if self.can_add_handler("ingress-public", handlers):
            self.ingress_public = sunbeam_rhandlers.IngressPublicHandler(
                self,
                "ingress-public",
                self.service_name,
                self.default_public_ingress_port,
                self.ingress_healthcheck_params,
                self._ingress_changed,
                "ingress-public" in self.mandatory_relations,
            )
            handlers.append(self.ingress_public)
        if self.can_add_handler("identity-service", handlers):
            self.id_svc = sunbeam_rhandlers.IdentityServiceRequiresHandler(
                self,
                "identity-service",
                self.configure_charm,
                self.service_endpoints,
                str(self.model.config["region"]),
                "identity-service" in self.mandatory_relations,
            )
            handlers.append(self.id_svc)
        return super().get_relation_handlers(handlers)

    def _ingress_changed(self, event: ops.framework.EventBase) -> None:
        """Ingress changed callback.

        Invoked when the data on the ingress relation has changed. This will
        update the relevant endpoints with the identity service, and then
        call the configure_charm.
        """
        logger.debug("Received an ingress_changed event")
        if hasattr(self, "id_svc"):
            logger.debug(
                "Updating service endpoints after ingress relation changed."
            )
            try:
                self.id_svc.update_service_endpoints(self.service_endpoints)
            except (AttributeError, KeyError):
                pass

        self.configure_charm(event)

    def service_url(self, hostname: str) -> str:
        """Service url for accessing this service via the given hostname."""
        return f"http://{hostname}:{self.default_public_ingress_port}"

    @property
    def public_ingress_address(self) -> str:
        """IP address or hostname for access to this service."""
        from lightkube.core.client import (
            Client,
        )
        from lightkube.resources.core_v1 import (
            Service,
        )

        client = Client()
        charm_service = client.get(
            Service, name=self.app.name, namespace=self.model.name
        )
        public_address = None
        status = charm_service.status
        if status:
            load_balancer_status = status.loadBalancer
            if load_balancer_status:
                ingress_addresses = load_balancer_status.ingress
                if ingress_addresses:
                    logger.debug(
                        "Found ingress addresses on loadbalancer " "status"
                    )
                    ingress_address = ingress_addresses[0]
                    addr = ingress_address.hostname or ingress_address.ip
                    if addr:
                        logger.debug(
                            "Using ingress address from loadbalancer "
                            f"as {addr}"
                        )
                        public_address = (
                            ingress_address.hostname or ingress_address.ip
                        )

        if not public_address:
            binding = self.model.get_binding("identity-service")
            if binding and binding.network and binding.network.ingress_address:
                public_address = str(binding.network.ingress_address)

        if not public_address:
            raise sunbeam_guard.WaitingExceptionError(
                "No public address found for service"
            )

        return public_address

    @property
    def public_url(self) -> str:
        """Url for accessing the public endpoint for this service."""
        try:
            if self.ingress_public.url:
                logger.debug(
                    "Ingress-public relation found, returning "
                    "ingress-public.url of: %s",
                    self.ingress_public.url,
                )
                return self.add_explicit_port(self.ingress_public.url)
        except (AttributeError, KeyError):
            pass

        return self.internal_url

    @property
    def admin_url(self) -> str:
        """Url for accessing the admin endpoint for this service.

        Fallback to k8s resolvable hostname if no identity-service relation.
        """
        binding = self.model.get_binding("identity-service")
        if binding and binding.network and binding.network.ingress_address:
            return self.add_explicit_port(
                self.service_url(str(binding.network.ingress_address))
            )
        return self.add_explicit_port(self.service_url(self.service_dns))

    @property
    def internal_url(self) -> str:
        """Url for accessing the internal endpoint for this service."""
        if hasattr(self, "ingress_internal"):
            if self.ingress_internal.url:
                logger.debug(
                    "Ingress-internal relation found, returning "
                    "ingress_internal.url of: %s",
                    self.ingress_internal.url,
                )
                return self.add_explicit_port(self.ingress_internal.url)

        binding = self.model.get_binding("identity-service")
        if binding and binding.network and binding.network.ingress_address:
            return self.add_explicit_port(
                self.service_url(str(binding.network.ingress_address))
            )
        return self.add_explicit_port(self.service_url(self.service_dns))

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

    @property
    def container_configs(self) -> list[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    self.service_conf,
                    self.service_user,
                    self.service_group,
                )
            ]
        )
        return _cconfigs

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return self.service_name

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return self.service_name

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/{self.service_name}/{self.service_name}.conf"

    @property
    def config_contexts(self) -> list[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend(
            [
                sunbeam_config_contexts.WSGIWorkerConfigContext(
                    self, "wsgi_config"
                )
            ]
        )
        return _cadapters

    @property
    def wsgi_container_name(self) -> str:
        """Name of the WSGI application container."""
        return self.service_name

    @property
    def default_public_ingress_port(self) -> int:
        """Port to use for ingress access to service."""
        raise NotImplementedError

    @property
    def db_sync_container_name(self) -> str:
        """Name of Containerto run db sync from."""
        return self.wsgi_container_name

    @property
    def healthcheck_period(self) -> str:
        """Healthcheck period for the service."""
        return "10s"  # Default value in pebble

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return f"http://localhost:{self.default_public_ingress_port}/"

    @property
    def healthcheck_http_timeout(self) -> str:
        """Healthcheck HTTP timeout for the service."""
        return "3s"

    def open_ports(self):
        """Register ports in underlying cloud."""
        self.unit.open_port("tcp", self.default_public_ingress_port)

    def add_explicit_port(self, org_url: str) -> str:
        """Update a url to add an explicit port.

        Keystone auth endpoint parsing can give odd results if
        an explicit port is missing.
        """
        url = urllib.parse.urlparse(org_url)
        new_netloc = url.netloc
        if not url.port:
            if url.scheme == "http":
                new_netloc = url.netloc + ":80"
            elif url.scheme == "https":
                new_netloc = url.netloc + ":443"
        return urllib.parse.urlunparse(
            (
                url.scheme,
                new_netloc,
                url.path,
                url.params,
                url.query,
                url.fragment,
            )
        )


class OSBaseOperatorCharmSnap(OSBaseOperatorCharm):
    """Base charm class for snap based charms."""

    def __init__(self, framework):
        super().__init__(framework)
        self.snap_module = self._import_snap()

        self.framework.observe(
            self.on.install,
            self._on_install,
        )

    def _import_snap(self):
        import charms.operator_libs_linux.v2.snap as snap

        return snap

    def _on_install(self, _: ops.InstallEvent):
        """Run install on this unit."""
        self.ensure_snap_present()

    @functools.cache
    def get_snap(self) -> "snap.Snap":
        """Return snap object."""
        return self.snap_module.SnapCache()[self.snap_name]

    @property
    def snap_name(self) -> str:
        """Return snap name."""
        raise NotImplementedError

    @property
    def snap_channel(self) -> str:
        """Return snap channel."""
        raise NotImplementedError

    def ensure_snap_present(self):
        """Install snap if it is not already present."""
        try:
            snap_svc = self.get_snap()

            if not snap_svc.present:
                snap_svc.ensure(
                    self.snap_module.SnapState.Latest,
                    channel=self.snap_channel,
                )
        except self.snap_module.SnapError as e:
            logger.error(
                "An exception occurred when installing %s. Reason: %s",
                self.snap_name,
                e.message,
            )

    def ensure_services_running(self, enable: bool = True) -> None:
        """Ensure snap services are up."""
        snap_svc = self.get_snap()
        snap_svc.start(enable=enable)

    def stop_services(self, relation: set[str] | None = None) -> None:
        """Stop snap services."""
        snap_svc = self.get_snap()
        snap_svc.stop(disable=True)

    def set_snap_data(self, snap_data: Mapping, namespace: str | None = None):
        """Set snap data on local snap.

        Setting keys with 3 level or more of indentation is not yet supported.
        `namespace` offers the possibility to work as if it was supported.
        """
        snap_svc = self.get_snap()
        new_settings = {}
        try:
            old_settings = snap_svc.get(namespace, typed=True)
        except self.snap_module.SnapError:
            old_settings = {}

        for key, new_value in snap_data.items():
            key_split = key.split(".")
            if len(key_split) == 2:
                group, subkey = key_split
                old_value = old_settings.get(group, {}).get(subkey)
            else:
                old_value = old_settings.get(key)
            if old_value is not None and old_value != new_value:
                new_settings[key] = new_value
            # Setting a value to None will unset the value from the snap,
            # which will fail if the value was never set.
            elif new_value is not None:
                new_settings[key] = new_value

        if new_settings:
            if namespace is not None:
                new_settings = {namespace: new_settings}
            logger.debug(f"Applying new snap settings {new_settings}")
            snap_svc.set(new_settings, typed=True)
        else:
            logger.debug("Snap settings do not need updating")

    def configure_snap(self, event: ops.EventBase) -> None:
        """Run configuration on managed snap."""

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.ensure_snap_present()
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.configure_snap(event)
        self.ensure_services_running()
        self._state.unit_bootstrapped = True


class OSCinderVolumeDriverOperatorCharm(OSBaseOperatorCharmSnap):
    """Base class charms for Cinder volume drivers.

    Operators implementing this class are subordinates charm that are not
    responsible for installing / managing the snap.
    Their only duty is to provide a backend configuration to the
    snap managed by the principal unit.
    """

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self._state.set_default(volume_ready=False)

    @property
    def backend_key(self) -> str:
        """Key for backend configuration."""
        raise NotImplementedError

    def ensure_snap_present(self):
        """No-op."""

    def ensure_services_running(self, enable: bool = True) -> None:
        """No-op."""

    def stop_services(self, relation: set[str] | None = None) -> None:
        """No-op."""

    @property
    def snap_name(self) -> str:
        """Return snap name."""
        snap_name = self.cinder_volume.interface.snap_name()

        if snap_name is None:
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting for snap name from cinder-volume relation"
            )

        return snap_name

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        self.cinder_volume = sunbeam_rhandlers.CinderVolumeRequiresHandler(
            self,
            "cinder-volume",
            self.backend_key,
            self.volume_ready,
            mandatory="cinder-volume" in self.mandatory_relations,
        )
        handlers.append(self.cinder_volume)
        return super().get_relation_handlers(handlers)

    def volume_ready(self, event) -> None:
        """Event handler for bootstrap of service when api services are ready."""
        self._state.volume_ready = True
        self.configure_charm(event)

    def configure_snap(self, event: ops.EventBase) -> None:
        """Configure backend for cinder volume driver."""
        if not bool(self._state.volume_ready):
            raise sunbeam_guard.WaitingExceptionError("Volume not ready")
        backend_context = self.get_backend_configuration()
        self.set_snap_data(backend_context, namespace=self.backend_key)
        self.cinder_volume.interface.set_ready()

    def get_backend_configuration(self) -> Mapping:
        """Get backend configuration."""
        raise NotImplementedError
