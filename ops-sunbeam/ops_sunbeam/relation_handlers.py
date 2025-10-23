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

"""Base classes for defining a charm using the Operator framework."""

import abc
import hashlib
import json
import logging
import secrets
import string
import typing
from typing import (
    Any,
    Callable,
    Dict,
)
from urllib.parse import (
    urlparse,
)

import ops.charm
import ops.framework
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.interfaces as sunbeam_interfaces
import ops_sunbeam.tracing as sunbeam_tracing
from ops import (
    ModelError,
)
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    SecretNotFoundError,
    Unit,
    UnknownStatus,
    WaitingStatus,
)
from ops_sunbeam.core import (
    PostInitMeta,
    RelationDataMapping,
)

if typing.TYPE_CHECKING:
    import charms.ceilometer_k8s.v0.ceilometer_service as ceilometer_service
    import charms.certificate_transfer_interface.v0.certificate_transfer as certificate_transfer
    import charms.cinder_ceph_k8s.v0.ceph_access as ceph_access
    import charms.cinder_volume.v0.cinder_volume as sunbeam_cinder_volume
    import charms.data_platform_libs.v0.data_interfaces as data_interfaces
    import charms.gnocchi_k8s.v0.gnocchi_service as gnocchi_service
    import charms.horizon_k8s.v0.trusted_dashboard as trusted_dashboard
    import charms.keystone_k8s.v0.identity_credentials as identity_credentials
    import charms.keystone_k8s.v0.identity_resource as identity_resource
    import charms.keystone_k8s.v1.identity_service as identity_service
    import charms.loki_k8s.v1.loki_push_api as loki_push_api
    import charms.nova_k8s.v0.nova_service as nova_service
    import charms.rabbitmq_k8s.v0.rabbitmq as rabbitmq
    import charms.sunbeam_libs.v0.service_readiness as service_readiness
    import charms.tempo_k8s.v2.tracing as tracing
    import charms.tls_certificates_interface.v3.tls_certificates as tls_certificates
    import charms.traefik_k8s.v0.traefik_route as traefik_route
    import charms.traefik_k8s.v2.ingress as ingress
    import interface_ceph_client.ceph_client as ceph_client  # type: ignore [import-untyped]
    from ops_sunbeam.charm import (
        OSBaseOperatorCharm,
    )


logger = logging.getLogger(__name__)

ERASURE_CODED = "erasure-coded"
REPLICATED = "replicated"


@sunbeam_tracing.trace_type
class RelationHandler(ops.framework.Object, metaclass=PostInitMeta):
    """Base handler class for relations.

    A relation handler is used to manage a charms interaction with a relation
    interface. This includes:

    1) Registering handlers to process events from the interface. The last
       step of these handlers is to make a callback to a specified method
       within the charm `callback_f`
    2) Expose a `ready` property so the charm can check a relations readiness
    3) A `context` method which returns a dict which pulls together data
       received and sent on an interface.
    """

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(
            charm,
            # Ensure we can have multiple instances of a relation handler,
            # but only one per relation.
            key=type(self).__name__ + "_" + relation_name,
        )
        self.charm = charm
        self.relation_name = relation_name
        self.callback_f = callback_f
        self.mandatory = mandatory
        self.status = compound_status.Status(self.relation_name)
        self.charm.status_pool.add(self.status)

    def __post_init__(self):
        """Run post init."""
        self.interface = self.setup_event_handler()
        self.set_status(self.status)

    def set_status(self, status: compound_status.Status) -> None:
        """Set the status based on current state.

        Will be called once, during construction,
        after everything else is initialised.
        Override this in a child class if custom logic should be used.
        """
        if not self.model.relations.get(self.relation_name):
            if self.mandatory:
                status.set(BlockedStatus("integration missing"))
            else:
                status.set(UnknownStatus())
        elif self.ready:
            status.set(ActiveStatus(""))
        else:
            status.set(WaitingStatus("integration incomplete"))

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for the relation.

        This method must be overridden in concrete class
        implementations.
        """
        raise NotImplementedError

    def get_interface(self) -> tuple[ops.Object, str]:
        """Return the interface that this handler encapsulates.

        This is a combination of the interface object and the
        name of the relation its wired into.
        """
        return self.interface, self.relation_name

    def interface_properties(self) -> dict:
        """Extract properties of the interface."""
        property_names = [
            p
            for p in dir(self.interface)
            if isinstance(getattr(type(self.interface), p, None), property)
        ]
        properties = {
            p: getattr(self.interface, p)
            for p in property_names
            if not p.startswith("_") and p not in ["model"]
        }
        return properties

    @property
    def ready(self) -> bool:
        """Determine with the relation is ready for use."""
        raise NotImplementedError

    def context(self) -> dict:
        """Pull together context for rendering templates."""
        return self.interface_properties()

    def update_relation_data(self):
        """Update relation outside of relation context."""
        raise NotImplementedError


@sunbeam_tracing.trace_type
class IngressHandler(RelationHandler):
    """Base class to handle Ingress relations."""

    interface: "ingress.IngressPerAppRequirer"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        service_name: str,
        default_ingress_port: int,
        ingress_healthcheck_params: Dict[str, Any],
        callback_f: Callable,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.default_ingress_port = default_ingress_port
        self.service_name = service_name
        self.ingress_healthcheck_params = ingress_healthcheck_params

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an Ingress relation."""
        logger.debug("Setting up ingress event handler")
        from charms.traefik_k8s.v2.ingress import (
            IngressPerAppRequirer,
        )

        interface = sunbeam_tracing.trace_type(IngressPerAppRequirer)(
            self.charm,
            self.relation_name,
            port=self.default_ingress_port,
            healthcheck_params=self.ingress_healthcheck_params,
        )
        self.framework.observe(interface.on.ready, self._on_ingress_ready)
        self.framework.observe(interface.on.revoked, self._on_ingress_revoked)
        return interface

    def _on_ingress_ready(self, event) -> None:  # noqa: ANN001
        """Handle ingress relation changed events.

        `event` is an instance of
        `charms.traefik_k8s.v2.ingress.IngressPerAppReadyEvent`.
        """
        url = self.url
        logger.debug(f"Received url: {url}")
        if not url:
            return

        self.callback_f(event)

    def _on_ingress_revoked(self, event) -> None:  # noqa: ANN001
        """Handle ingress relation revoked event.

        `event` is an instance of
        `charms.traefik_k8s.v2.ingress.IngressPerAppRevokedEvent`
        """
        # Callback call to update keystone endpoints
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether the handler is ready for use."""
        from charms.traefik_k8s.v2.ingress import (
            DataValidationError,
        )

        try:
            url = self.interface.url
        except DataValidationError:
            logger.debug(
                "Failed to fetch relation's url,"
                " the root cause might a change to V2 Ingress, "
                "in this case, this error should go away.",
                exc_info=True,
            )
            return False

        if url:
            return True

        return False

    @property
    def url(self) -> str | None:
        """Return the URL used by the remote ingress service."""
        if not self.ready:
            return None

        return self.interface.url

    def context(self) -> dict:
        """Context containing ingress data."""
        parse_result = urlparse(self.url)
        return {
            "ingress_path": parse_result.path,
        }


@sunbeam_tracing.trace_type
class IngressInternalHandler(IngressHandler):
    """Handler for Ingress relations on internal interface."""


@sunbeam_tracing.trace_type
class IngressPublicHandler(IngressHandler):
    """Handler for Ingress relations on public interface."""


@sunbeam_tracing.trace_type
class DBHandler(RelationHandler):
    """Handler for DB relations."""

    interface: "data_interfaces.DatabaseRequires"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        database: str,
        mandatory: bool = False,
        external_access: bool = False,
    ) -> None:
        """Run constructor."""
        # a database name as requested by the charm.
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.database_name = database
        self.external_access = external_access

    def update_relation_data(self):
        """Update relation outside of relation context."""
        self._update_mysql_data()

    def _update_mysql_data(self):
        """Publish mysql encoded fields."""
        if not self.charm.model.unit.is_leader():
            return

        relation = self.get_relation()
        if relation is None or not relation.active:
            return

        # note(gboutry): Need to mimic a created_event
        # to ensure mysql db publishes all the data
        try:
            self.interface._on_relation_created_event(
                ops.RelationCreatedEvent(
                    self.handle, relation, relation.app, None
                )
            )
        except ops.ModelError:
            logger.debug("Failed to publish encoded fields.", exc_info=True)

    def get_relation(self) -> ops.Relation | None:
        """Fetch the relation for the handler.

        We're guaranteed to have only one relation for a given
        database.
        """
        for relation in self.model.relations[self.relation_name]:
            return relation
        return None

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for a MySQL relation."""
        logger.debug("Setting up DB event handler")
        # Import here to avoid import errors if ops_sunbeam is being used
        # with a charm that doesn't want a DBHandler
        # and doesn't install this database_requires library.
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequires,
        )

        # Alias is required to events for this db
        # from trigger handlers for other dbs.
        # It also must be a valid python identifier.
        alias = self.relation_name.replace("-", "_")
        # tracing this library is currently failing
        # implement when either one of these is fixed:
        # https://github.com/canonical/tempo-k8s-operator/issues/155
        # https://github.com/canonical/data-platform-libs/issues/186
        db = DatabaseRequires(
            self.charm,
            self.relation_name,
            self.database_name,
            relations_aliases=[alias],
            external_node_connectivity=self.external_access,
        )
        self.framework.observe(
            # db.on[f"{alias}_database_created"], # this doesn't work because:
            # RuntimeError: Framework.observe requires a BoundEvent as
            # second parameter, got <ops.framework.PrefixedEvents object ...
            getattr(db.on, f"{alias}_database_created"),
            self._on_database_updated,
        )
        self.framework.observe(
            getattr(db.on, f"{alias}_endpoints_changed"),
            self._on_database_updated,
        )

        # Gone away events are not handled by the database interface library.
        # So handle them in this class
        self.framework.observe(
            self.charm.on[self.relation_name].relation_broken,
            self._on_database_relation_broken,
        )

        # this will be set to self.interface in parent class
        return db

    def _on_database_updated(
        self,
        event: typing.Union[
            "data_interfaces.DatabaseCreatedEvent",
            "data_interfaces.DatabaseEndpointsChangedEvent",
            "data_interfaces.DatabaseReadOnlyEndpointsChangedEvent",
        ],
    ) -> None:
        """Handle database change events."""
        if not (event.username or event.password or event.endpoints):
            return

        data = event.relation.data[event.relation.app]
        display_data = {k: v for k, v in data.items()}
        if "password" in display_data:
            display_data["password"] = "REDACTED"
        logger.info(f"Received data: {display_data}")
        self.callback_f(event)

    def _on_database_relation_broken(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle database gone away event."""
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    def get_relation_data(self) -> RelationDataMapping:
        """Load the data from the relation for consumption in the handler."""
        # there is at most one relation for a database
        relation = self.get_relation()
        if relation:
            return relation.data[relation.app]
        return {}

    @property
    def ready(self) -> bool:
        """Whether the handler is ready for use."""
        data = self.get_relation_data()
        return bool(data.get("endpoints") and data.get("secret-user"))

    def context(self) -> dict:
        """Context containing database connection data."""
        if not self.ready:
            return {}

        data = self.get_relation_data()
        database_name = self.database_name
        database_host = data["endpoints"]
        user_secret = self.model.get_secret(id=data["secret-user"])
        secret_data = user_secret.get_content(refresh=True)
        database_user = secret_data["username"]
        database_password = secret_data["password"]
        database_type = "mysql+pymysql"
        has_tls = data.get("tls")
        tls_ca = data.get("tls-ca")

        connection = (
            f"{database_type}://{database_user}:{database_password}"
            f"@{database_host}/{database_name}"
        )
        if has_tls:
            connection = connection + f"?ssl_ca={tls_ca}"

        # This context ends up namespaced under the relation name
        # (normalised to fit a python identifier - s/-/_/),
        # and added to the context for jinja templates.
        # eg. if this DBHandler is added with relation name api-database,
        # the database connection string can be obtained in templates with
        # `api_database.connection`.
        return {
            "database": database_name,
            "database_host": database_host,
            "database_password": database_password,
            "database_user": database_user,
            "database_type": database_type,
            "connection": connection,
        }


@sunbeam_tracing.trace_type
class RabbitMQHandler(RelationHandler):
    """Handler for managing a rabbitmq relation."""

    interface: "rabbitmq.RabbitMQRequires"
    DEFAULT_PORT = "5672"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        username: str,
        vhost: str,
        external_connectivity: bool,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.username = username
        self.vhost = vhost
        self.external_connectivity = external_connectivity

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an AMQP relation."""
        logger.debug("Setting up AMQP event handler")
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        import charms.rabbitmq_k8s.v0.rabbitmq as sunbeam_rabbitmq

        amqp = sunbeam_tracing.trace_type(sunbeam_rabbitmq.RabbitMQRequires)(
            self.charm,
            self.relation_name,
            self.username,
            self.vhost,
            self.external_connectivity,
        )
        self.framework.observe(amqp.on.ready, self._on_amqp_ready)
        self.framework.observe(amqp.on.goneaway, self._on_amqp_goneaway)
        return amqp

    def update_relation_data(self):
        """Update relation outside of relation context."""
        self.interface.request_access(
            self.username,
            self.vhost,
            self.external_connectivity,
        )

    def _on_amqp_ready(self, event: ops.framework.EventBase) -> None:
        """Handle AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    def _on_amqp_goneaway(self, event: ops.framework.EventBase) -> None:
        """Handle AMQP change events."""
        # Goneaway is only emitted when the interface considers
        # that the relation is broken
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.password) and bool(
                self.interface.hostname
            )
        except (AttributeError, KeyError):
            return False

    def context(self) -> dict:
        """Context containing AMQP connection data."""
        try:
            host = self.interface.hostname
        except (AttributeError, KeyError):
            return {}
        if not host:
            return {}
        ctxt = super().context()
        ctxt["hostname"] = host
        ctxt["port"] = ctxt.get("ssl_port") or self.DEFAULT_PORT
        transport_url_host = "{}:{}@{}:{}".format(
            self.username,
            ctxt["password"],
            host,  # TODO deal with IPv6
            ctxt["port"],
        )

        transport_url = "rabbit://{}/{}".format(transport_url_host, self.vhost)
        ctxt["transport_url"] = transport_url
        return ctxt


@sunbeam_tracing.trace_type
class AMQPHandler(RabbitMQHandler):
    """Backwards compatibility class for older library consumers."""

    pass


@sunbeam_tracing.trace_type
class IdentityServiceRequiresHandler(RelationHandler):
    """Handler for managing a identity-service relation."""

    interface: "identity_service.IdentityServiceRequires"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        service_endpoints: list[dict],
        region: str,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.service_endpoints = service_endpoints
        self.region = region

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        import charms.keystone_k8s.v1.identity_service as sun_id

        id_svc = sunbeam_tracing.trace_type(sun_id.IdentityServiceRequires)(
            self.charm, self.relation_name, self.service_endpoints, self.region
        )
        self.framework.observe(
            id_svc.on.ready, self._on_identity_service_ready
        )
        self.framework.observe(
            id_svc.on.goneaway, self._on_identity_service_goneaway
        )
        return id_svc

    def _on_identity_service_ready(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    def _on_identity_service_goneaway(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle identity service gone away event."""
        # Goneaway is only emitted when the interface considers
        # that the relation is broken or departed.
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    def update_relation_data(self):
        """Update relation outside of relation context."""
        if self.model.get_relation(self.relation_name):
            self.interface.register_services(
                self.service_endpoints, self.region
            )

    def update_service_endpoints(self, service_endpoints: list[dict]) -> None:
        """Update service endpoints on the relation."""
        self.service_endpoints = service_endpoints
        self.interface.register_services(service_endpoints, self.region)

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.service_password)
        except (AttributeError, KeyError, ModelError):
            return False


@sunbeam_tracing.trace_type
class BasePeerHandler(RelationHandler):
    """Base handler for managing a peers relation."""

    interface: sunbeam_interfaces.OperatorPeers
    LEADER_READY_KEY = "leader_ready"

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for peer relation."""
        logger.debug("Setting up peer event handler")
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        peer_int = sunbeam_tracing.trace_type(
            sunbeam_interfaces.OperatorPeers
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            peer_int.on.peers_relation_joined, self._on_peers_relation_joined
        )
        self.framework.observe(
            peer_int.on.peers_data_changed, self._on_peers_data_changed
        )
        return peer_int

    def _on_peers_relation_joined(
        self, event: ops.framework.EventBase
    ) -> None:
        """Process peer joined event."""
        self.callback_f(event)

    def _on_peers_data_changed(self, event: ops.framework.EventBase) -> None:
        """Process peer data changed event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether the handler is complete."""
        return bool(self.interface.peers_rel)

    def context(self) -> dict:
        """Return all app data set on the peer relation."""
        try:
            translators = str.maketrans({"/": "_", ".": "_", "-": "_"})
            _db = {
                k.translate(translators): v
                for k, v in self.interface.get_all_app_data().items()
            }
            return _db
        except (AttributeError, KeyError):
            return {}

    def set_app_data(self, settings: RelationDataMapping) -> None:
        """Store data in peer app db."""
        self.interface.set_app_data(settings)

    def get_app_data(self, key: str) -> str | None:
        """Retrieve data from the peer relation."""
        return self.interface.get_app_data(key)

    def leader_get(self, key: str) -> str | None:
        """Retrieve data from the peer relation."""
        return self.interface.get_app_data(key)

    def leader_set(
        self, settings: RelationDataMapping | None, **kwargs
    ) -> None:
        """Store data in peer app db."""
        settings = settings or {}
        settings.update(kwargs)
        self.set_app_data(settings)

    def set_leader_ready(self) -> None:
        """Tell peers the leader is ready."""
        self.set_app_data({self.LEADER_READY_KEY: json.dumps(True)})

    def is_leader_ready(self) -> bool:
        """Whether the leader has announced it is ready."""
        ready = self.get_app_data(self.LEADER_READY_KEY)
        if ready is None:
            return False
        else:
            return json.loads(ready)

    def set_unit_data(self, settings: dict[str, str]) -> None:
        """Publish settings on the peer unit data bag."""
        self.interface.set_unit_data(settings)

    def get_all_unit_values(
        self, key: str, include_local_unit: bool = False
    ) -> list[str]:
        """Retrieve value for key from all related units.

        :param include_local_unit: Include value set by local unit
        """
        return self.interface.get_all_unit_values(
            key, include_local_unit=include_local_unit
        )


@sunbeam_tracing.trace_type
class CephClientHandler(RelationHandler):
    """Handler for ceph-client interface."""

    interface: "ceph_client.CephClientRequires"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        allow_ec_overwrites: bool = True,
        app_name: str | None = None,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.allow_ec_overwrites = allow_ec_overwrites
        self.app_name = app_name

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an ceph-client interface."""
        logger.debug("Setting up ceph-client event handler")
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        import interface_ceph_client.ceph_client as ceph_client

        ceph = sunbeam_tracing.trace_type(ceph_client.CephClientRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ceph.on.pools_available, self._on_pools_available
        )
        self.framework.observe(ceph.on.broker_available, self.request_pools)
        return ceph

    def _on_pools_available(self, event: ops.framework.EventBase) -> None:
        """Handle pools available event."""
        # Ready is only emitted when the interface considers
        # that the relation is complete
        self.callback_f(event)

    def request_pools(self, event: ops.framework.EventBase) -> None:
        """Request Ceph pool creation when interface broker is ready.

        The default handler will automatically request erasure-coded
        or replicated pools depending on the configuration of the
        charm from which the handler is being used.

        To provide charm specific behaviour, subclass the default
        handler and use the required broker methods on the underlying
        interface object.
        """
        config = self.model.config.get
        data_pool_name = (
            config("rbd-pool-name")
            or config("rbd-pool")
            or self.charm.app.name
        )
        # schema defined as str
        metadata_pool_name: str = typing.cast(
            str,
            config("ec-rbd-metadata-pool")
            or f"{self.charm.app.name}-metadata",
        )
        # schema defined as int and with a default
        # weight is then managed as a float.
        weight = float(typing.cast(int, config("ceph-pool-weight")))
        # schema defined as int and with a default
        replicas = typing.cast(int, config("ceph-osd-replication-count"))
        # TODO: add bluestore compression options
        if config("pool-type") == ERASURE_CODED:
            # General EC plugin config
            # schema defined as str and with a default
            plugin = typing.cast(str, config("ec-profile-plugin"))
            technique = config("ec-profile-technique")
            device_class = config("ec-profile-device-class")
            bdm_k = config("ec-profile-k")
            bdm_m = config("ec-profile-m")
            # LRC plugin config
            bdm_l = config("ec-profile-locality")
            crush_locality = config("ec-profile-crush-locality")
            # SHEC plugin config
            bdm_c = config("ec-profile-durability-estimator")
            # CLAY plugin config
            bdm_d = config("ec-profile-helper-chunks")
            scalar_mds = config("ec-profile-scalar-mds")
            # Profile name
            profile_name = (
                config("ec-profile-name") or f"{self.charm.app.name}-profile"
            )
            # Metadata sizing is approximately 1% of overall data weight
            # but is in effect driven by the number of rbd's rather than
            # their size - so it can be very lightweight.
            metadata_weight = weight * 0.01
            # Resize data pool weight to accommodate metadata weight
            weight = weight - metadata_weight
            # Create erasure profile
            self.interface.create_erasure_profile(
                name=profile_name,
                k=bdm_k,
                m=bdm_m,
                lrc_locality=bdm_l,
                lrc_crush_locality=crush_locality,
                shec_durability_estimator=bdm_c,
                clay_helper_chunks=bdm_d,
                clay_scalar_mds=scalar_mds,
                device_class=device_class,
                erasure_type=plugin,
                erasure_technique=technique,
            )

            # Create EC data pool
            self.interface.create_erasure_pool(
                name=data_pool_name,
                erasure_profile=profile_name,
                weight=weight,
                allow_ec_overwrites=self.allow_ec_overwrites,
                app_name=self.app_name,
            )
            # Create EC metadata pool
            self.interface.create_replicated_pool(
                name=metadata_pool_name,
                replicas=replicas,
                weight=metadata_weight,
                app_name=self.app_name,
            )
        else:
            self.interface.create_replicated_pool(
                name=data_pool_name,
                replicas=replicas,
                weight=weight,
                app_name=self.app_name,
            )

    @property
    def ready(self) -> bool:
        """Whether handler ready for use."""
        return self.interface.pools_available

    @property
    def key(self) -> str | None:
        """Retrieve the cephx key provided for the application."""
        return self.interface.get_relation_data().get("key")

    def context(self) -> dict:
        """Context containing Ceph connection data."""
        ctxt = super().context()
        data = self.interface.get_relation_data()
        # mon_hosts is a list of sorted host strings
        mon_hosts = typing.cast(list[str] | None, data.get("mon_hosts"))
        if not mon_hosts:
            return {}
        ctxt["mon_hosts"] = ",".join(mon_hosts)
        ctxt["auth"] = data.get("auth")
        ctxt["key"] = data.get("key")
        ctxt["rbd_features"] = None
        return ctxt


class _StoreEntry(typing.TypedDict, total=False):
    """Type definition for a store entry."""

    private_key: str
    csr: str


class _Store(abc.ABC):

    @abc.abstractmethod
    def ready(self) -> bool:
        """Check if store is ready."""
        ...

    @abc.abstractmethod
    def get_entries(self) -> dict[str, _StoreEntry]:
        """Get store dict from relation data."""
        ...

    @abc.abstractmethod
    def save_entries(self, entries: dict[str, _StoreEntry]):
        """Save store dict to relation data."""
        ...

    def get_entry(self, name: str) -> _StoreEntry | None:
        """Return store entry."""
        if not self.ready():
            logger.debug("Store not ready, cannot get entry.")
            return None
        return self.get_entries().get(name)

    def save_entry(self, name: str, entry: _StoreEntry):
        """Save store entry."""
        if not self.ready():
            logger.debug("Store not ready, cannot set entry.")
            return
        store = self.get_entries()
        store[name] = entry
        self.save_entries(store)

    def delete_entry(self, name: str):
        """Delete store entry."""
        if not self.ready():
            logger.debug("Store not ready, cannot delete entry.")
            return
        store = self.get_entries()
        store.pop(name, None)
        self.save_entries(store)

    def get_private_key(self, name: str) -> str | None:
        """Return private key."""
        if entry := self.get_entry(name):
            return entry.get("private_key")
        return None

    def get_csr(self, name: str) -> str | None:
        """Return csr."""
        if entry := self.get_entry(name):
            return entry.get("csr")
        return None

    def set_private_key(self, name: str, private_key: str):
        """Update private key."""
        entry = self.get_entry(name) or {}
        entry["private_key"] = private_key
        self.save_entry(name, entry)

    def set_csr(self, name: str, csr: bytes):
        """Update csr."""
        entry = self.get_entry(name) or {}
        entry["csr"] = csr.decode()
        self.save_entry(name, entry)

    def delete_csr(self, name: str):
        """Delete csr."""
        entry = self.get_entry(name) or {}
        entry.pop("csr", None)
        self.save_entry(name, entry)


@sunbeam_tracing.trace_type
class TlsCertificatesHandler(RelationHandler):
    """Handler for certificates interface."""

    interface: "tls_certificates.TLSCertificatesRequiresV3"
    store: _Store

    class PeerStore(_Store):
        """Store private key secret id in peer storage relation."""

        STORE_KEY: str = "tls-store"

        def __init__(
            self, relation: ops.Relation, entity: ops.Unit | ops.Application
        ):
            self.relation = relation
            self.entity = entity

        def ready(self) -> bool:
            """Check if store is ready."""
            return bool(self.relation) and self.relation.active

        def get_entries(self) -> dict[str, _StoreEntry]:
            """Get store dict from relation data."""
            if not self.ready():
                return {}
            return json.loads(
                self.relation.data[self.entity].get(self.STORE_KEY, "{}")
            )

        def save_entries(self, entries: dict[str, _StoreEntry]):
            """Save store dict to relation data."""
            if self.ready():
                self.relation.data[self.entity][self.STORE_KEY] = json.dumps(
                    entries
                )

    class LocalDBStore(_Store):
        """Store private key sercret id in local unit db.

        This is a fallback for when the peer relation is not
        present.
        """

        def __init__(self, state_db):
            self.state_db = state_db
            try:
                self.state_db.tls_store
            except AttributeError:
                self.state_db.tls_store = "{}"

        def ready(self) -> bool:
            """Check if store is ready."""
            return True

        def get_entries(self) -> dict[str, _StoreEntry]:
            """Get store dict from relation data."""
            return json.loads(self.state_db.tls_store)

        def save_entries(self, entries: dict[str, _StoreEntry]):
            """Save store dict to relation data."""
            self.state_db.tls_store = json.dumps(entries)

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        sans_dns: list[str] | None = None,
        sans_ips: list[str] | None = None,
        mandatory: bool = False,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self._private_keys: dict[str, str] = {}
        self.sans_dns = sans_dns
        self.sans_ips = sans_ips
        try:
            peer_relation = self.model.get_relation("peers")
            # TODO(gboutry): fix type ignore
            self.store = self.PeerStore(peer_relation, self.get_entity())  # type: ignore[arg-type]
        except KeyError:
            if self.app_managed_certificates():
                raise RuntimeError(
                    "Application managed certificates require a peer relation"
                )
            self.store = self.LocalDBStore(charm._state)
        self.setup_private_keys()

    def get_entity(self) -> ops.Unit | ops.Application:
        """Return the entity for the key store.

        Defaults to the unit.
        """
        return self.charm.model.unit

    def i_am_allowed(self) -> bool:
        """Whether this unit is allowed to modify the store."""
        i_need_to_be_leader = self.app_managed_certificates()
        if i_need_to_be_leader:
            return self.charm.unit.is_leader()

        return True

    def app_managed_certificates(self) -> bool:
        """Whether the application manages its own certificates."""
        return isinstance(self.get_entity(), ops.Application)

    def key_names(self) -> list[str]:
        """Return the key names managed by this relation.

        First key is considered as default key.
        """
        return ["main"]

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
                subject=self.get_entity().name.replace("/", "-"),
                sans_dns=self.sans_dns,
                sans_ip=self.sans_ips,
            )
        }

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for tls relation."""
        logger.debug("Setting up certificates event handler")
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        from charms.tls_certificates_interface.v3.tls_certificates import (
            TLSCertificatesRequiresV3,
        )

        self.certificates = sunbeam_tracing.trace_type(
            TLSCertificatesRequiresV3
        )(self.charm, "certificates")

        self.framework.observe(
            self.charm.on.certificates_relation_joined,
            self._on_certificates_relation_joined,
        )
        self.framework.observe(
            self.charm.on.certificates_relation_broken,
            self._on_certificates_relation_broken,
        )
        self.framework.observe(
            self.certificates.on.certificate_available,
            self._on_certificate_available,
        )
        self.framework.observe(
            self.certificates.on.certificate_expiring,
            self._on_certificate_expiring,
        )
        self.framework.observe(
            self.certificates.on.certificate_invalidated,
            self._on_certificate_invalidated,
        )
        self.framework.observe(
            self.certificates.on.all_certificates_invalidated,
            self._on_all_certificate_invalidated,
        )
        return self.certificates

    def _setup_private_key(self, key: str):
        """Create and store private key if needed."""
        # Lazy import to ensure this lib is only required if the charm
        # has this relation.
        from charms.tls_certificates_interface.v3.tls_certificates import (
            generate_private_key,
        )

        if private_key_secret_id := self.store.get_private_key(key):
            logger.debug("Private key already present")
            try:
                private_key_secret = self.model.get_secret(
                    id=private_key_secret_id
                )
            except SecretNotFoundError:
                # When a unit is departing its secrets are removed by Juju.
                # So trying to access the secret will result in
                # SecretNotFoundError. Given this secret is set by this
                # unit and only consumed by this unit it is unlikely there
                # is any other reason for the secret to be missing.
                logger.debug(
                    "SecretNotFoundError not found, likely due to departing "
                    "unit."
                )
                # There's an entry, but the secret is missing
                # in the juju controller. Remove entry from
                # the store.
                self.store.delete_entry(key)
                return
            private_key_secret = self.model.get_secret(
                id=private_key_secret_id
            )
            self._private_keys[key] = private_key_secret.get_content(
                refresh=True
            )["private-key"]
            return

        self._private_keys[key] = generate_private_key().decode()
        private_key_secret = self.get_entity().add_secret(
            {"private-key": self._private_keys[key]},
            label=f"{self.get_entity().name}-{key}-private-key",
        )

        self.store.set_private_key(
            key, typing.cast(str, private_key_secret.id)
        )

    def setup_private_keys(self) -> None:
        """Create and store private key if needed."""
        if not self.i_am_allowed():
            logger.debug(
                "Unit is not allow to handle private keys, skipping setup"
            )
            return

        if not self.store.ready():
            logger.debug("Store not ready, cannot generate key")
            return

        keys = self.key_names()
        if not keys:
            raise RuntimeError("No keys to generate, this is always a bug.")

        for key in keys:
            self._setup_private_key(key)

    @property
    def private_key(self) -> str | None:
        """Private key for certificates.

        Return the first key from key_names.
        """
        if private_key := self._private_keys.get(self.key_names()[0]):
            return private_key
        return None

    def update_relation_data(self):
        """Request certificates outside of relation context."""
        if list(self.model.relations[self.relation_name]):
            self._request_certificates()
        else:
            logger.debug(
                "Not updating certificate request data, no relation found"
            )

    def _on_certificates_relation_joined(self, event: ops.EventBase) -> None:
        """Request certificates in response to relation join event."""
        self._request_certificates()

    def _request_certificates(self, renew=False):
        """Request certificates from remote provider."""
        if not self.i_am_allowed():
            logger.debug(
                "Unit is not allow to handle private keys, skipping setup"
            )
            return

        if not renew and self.ready:
            logger.debug("Certificate request already complete.")
            return

        keys = self.key_names()
        if set(keys) != set(self._private_keys.keys()):
            logger.debug("Not all private keys are setup, skipping request.")
            return

        csrs = self.csrs()

        if set(keys) != set(csrs.keys()):
            raise RuntimeError(
                "Mismatch between keys and csrs, this is always a bug."
            )

        for name, csr in csrs.items():
            previous_csr = self.store.get_csr(name)
            csr = csr.strip()
            if renew and previous_csr:
                self.certificates.request_certificate_renewal(
                    old_certificate_signing_request=previous_csr.encode(),
                    new_certificate_signing_request=csr,
                )
                self.store.set_csr(name, csr)
            elif previous_csr:
                logger.debug(
                    "CSR already exists for %s, skipping request.", name
                )
            else:
                self.certificates.request_certificate_creation(
                    certificate_signing_request=csr
                )
                self.store.set_csr(name, csr)

    def _on_certificates_relation_broken(self, event: ops.EventBase) -> None:
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    def _on_certificate_available(self, event: ops.EventBase) -> None:
        self.callback_f(event)

    def _on_certificate_expiring(self, event: ops.EventBase) -> None:
        self.status.set(ActiveStatus("Certificates are getting expired soon"))
        logger.warning("Certificate getting expired, requesting new ones.")
        self._request_certificates(renew=True)
        self.callback_f(event)

    def _on_certificate_invalidated(self, event: ops.EventBase) -> None:
        logger.warning("Certificate invalidated, requesting new ones.")
        if (
            self.i_am_allowed()
            and (relation := self.model.get_relation(self.relation_name))
            and relation.active
        ):
            self._request_certificates(renew=True)
        self.callback_f(event)

    def _on_all_certificate_invalidated(self, event: ops.EventBase) -> None:
        logger.warning(
            "Certificates invalidated, most likely a relation broken."
        )
        self.status.set(BlockedStatus("Certificates invalidated"))
        if self.i_am_allowed():
            for name in self.key_names():
                self.store.delete_csr(name)
        self.callback_f(event)

    def get_certs(self) -> list:
        """Return certificates."""
        # If certificates are managed at the app level
        # return all the certificates
        if self.app_managed_certificates():
            return self.interface.get_provider_certificates()
        # If the certificates are managed at the unit level
        # return the certificates for the unit
        return self.interface.get_assigned_certificates()

    @property
    def ready(self) -> bool:
        """Whether handler ready for use."""
        certs = self.get_certs()

        if len(certs) != len(self.key_names()):
            return False
        return True

    def context(self) -> dict:
        """Certificates context."""
        certs = self.get_certs()
        if len(certs) != len(self.key_names()):
            return {}
        ctxt = {}
        for name, entry in self.store.get_entries().items():
            csr = entry.get("csr")
            key = self._private_keys.get(name)
            if csr is None or key is None:
                logger.warning("Tls Store Entry %s is incomplete", name)
                continue
            for cert in certs:
                if cert.csr == csr:
                    ctxt.update(
                        {
                            "key_" + name: key,
                            "ca_cert_"
                            + name: cert.ca
                            + "\n"
                            + "\n".join(cert.chain),
                            "cert_" + name: cert.certificate,
                        }
                    )
            else:
                logger.debug("No certificate found for CSR %s", name)
        return ctxt


@sunbeam_tracing.trace_type
class IdentityCredentialsRequiresHandler(RelationHandler):
    """Handles the identity credentials relation on the requires side."""

    interface: "identity_credentials.IdentityCredentialsRequires"

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for identity-credentials relation."""
        import charms.keystone_k8s.v0.identity_credentials as identity_credentials

        logger.debug("Setting up the identity-credentials event handler")
        credentials_service = sunbeam_tracing.trace_type(
            identity_credentials.IdentityCredentialsRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            credentials_service.on.ready, self._credentials_ready
        )
        self.framework.observe(
            credentials_service.on.goneaway, self._credentials_goneaway
        )
        return credentials_service

    def _credentials_ready(self, event: ops.framework.EventBase) -> None:
        """React to credential ready event."""
        self.callback_f(event)

    def _credentials_goneaway(self, event: ops.framework.EventBase) -> None:
        """React to credential goneaway event."""
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    def update_relation_data(self):
        """Update relation outside of relation context."""
        if self.model.get_relation(self.relation_name):
            self.interface.request_credentials()

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.password)
        except (AttributeError, KeyError, ModelError):
            return False


@sunbeam_tracing.trace_type
class IdentityResourceRequiresHandler(RelationHandler):
    """Handles the identity resource relation on the requires side."""

    interface: "identity_resource.IdentityResourceRequires"

    def setup_event_handler(self):
        """Configure event handlers for an Identity resource relation."""
        import charms.keystone_k8s.v0.identity_resource as ops_svc

        logger.debug("Setting up Identity Resource event handler")
        ops_svc = sunbeam_tracing.trace_type(ops_svc.IdentityResourceRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ops_svc.on.provider_ready,
            self._on_provider_ready,
        )
        self.framework.observe(
            ops_svc.on.provider_goneaway,
            self._on_provider_goneaway,
        )
        self.framework.observe(
            ops_svc.on.response_available,
            self._on_response_available,
        )
        return ops_svc

    def _on_provider_ready(self, event) -> None:
        """Handles provider_ready  event."""
        logger.debug(
            "Identity ops provider available and ready to process any requests"
        )
        self.callback_f(event)

    def _on_provider_goneaway(self, event) -> None:
        """Handles provider_goneaway  event."""
        logger.info("Keystone provider not available process any requests")
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    def _on_response_available(self, event) -> None:
        """Handles response available  events."""
        logger.info("Handle response from identity ops")
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return self.interface.ready()


@sunbeam_tracing.trace_type
class CeilometerServiceRequiresHandler(RelationHandler):
    """Handle ceilometer service relation on the requires side."""

    interface: "ceilometer_service.CeilometerServiceRequires"

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for Ceilometer service relation."""
        import charms.ceilometer_k8s.v0.ceilometer_service as ceilometer_svc

        logger.debug("Setting up Ceilometer service event handler")
        svc = sunbeam_tracing.trace_type(
            ceilometer_svc.CeilometerServiceRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_changed,
            self._on_config_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_goneaway,
        )
        return svc

    def _on_config_changed(self, event: ops.framework.EventBase) -> None:
        """Handle config_changed  event."""
        logger.debug(
            "Ceilometer service provider config changed event received"
        )
        self.callback_f(event)

    def _on_goneaway(self, event: ops.framework.EventBase) -> None:
        """Handle gone_away  event."""
        logger.debug("Ceilometer service relation is departed/broken")
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.telemetry_secret)
        except (AttributeError, KeyError):
            return False


@sunbeam_tracing.trace_type
class CephAccessRequiresHandler(RelationHandler):
    """Handles the ceph access relation on the requires side."""

    interface: "ceph_access.CephAccessRequires"

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for ceph-access relation."""
        import charms.cinder_ceph_k8s.v0.ceph_access as ceph_access

        logger.debug("Setting up the ceph-access event handler")
        ceph_access_requires = sunbeam_tracing.trace_type(
            ceph_access.CephAccessRequires
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ceph_access_requires.on.ready, self._ceph_access_ready
        )
        self.framework.observe(
            ceph_access_requires.on.goneaway, self._ceph_access_goneaway
        )
        return ceph_access_requires

    def _ceph_access_ready(self, event: ops.framework.EventBase) -> None:
        """React to credential ready event."""
        self.callback_f(event)

    def _ceph_access_goneaway(self, event: ops.framework.EventBase) -> None:
        """React to credential goneaway event."""
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.ready)
        except (AttributeError, KeyError):
            return False

    def context(self) -> dict:
        """Context containing Ceph access data."""
        ctxt = super().context()
        data = self.interface.ceph_access_data
        ctxt["key"] = data.get("key")
        ctxt["uuid"] = data.get("uuid")
        return ctxt


ExtraOpsProcess = Callable[[ops.EventBase, dict], None]


@sunbeam_tracing.trace_type
class UserIdentityResourceRequiresHandler(RelationHandler):
    """Handle user management on IdentityResource relation."""

    interface: "identity_resource.IdentityResourceRequires"

    CREDENTIALS_SECRET_PREFIX = "user-identity-resource-"
    CONFIGURE_SECRET_PREFIX = "configure-credential-"

    resource_identifiers: frozenset[str] = frozenset(
        {
            "name",
            "email",
            "description",
            "domain",
            "project",
            "project_domain",
            "enable",
            "may_exist",
        }
    )

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        mandatory: bool,
        name: str,
        domain: str,
        email: str | None = None,
        description: str | None = None,
        project: str | None = None,
        project_domain: str | None = None,
        enable: bool = True,
        may_exist: bool = True,
        role: str | None = None,
        add_suffix: bool = False,
        rotate: ops.SecretRotate = ops.SecretRotate.NEVER,
        extra_ops: list[dict | Callable] | None = None,
        extra_ops_process: ExtraOpsProcess | None = None,
    ):
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.username = name
        self.charm = charm
        self.add_suffix = add_suffix
        # add_suffix is used to add suffix to username to create unique user
        self.role = role
        self.rotate = rotate
        self.extra_ops = extra_ops
        self.extra_ops_process = extra_ops_process

        self._params = {}
        _locals = locals()
        for keys in self.resource_identifiers:
            value = _locals.get(keys)
            if value is not None:
                self._params[keys] = value

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for the relation."""
        import charms.keystone_k8s.v0.identity_resource as id_ops

        logger.debug("Setting up Identity Resource event handler")
        ops_svc = sunbeam_tracing.trace_type(id_ops.IdentityResourceRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ops_svc.on.provider_ready,
            self._on_provider_ready,
        )
        self.framework.observe(
            ops_svc.on.provider_goneaway,
            self._on_provider_goneaway,
        )
        self.framework.observe(
            ops_svc.on.response_available,
            self._on_response_available,
        )
        self.framework.observe(
            self.charm.on.secret_changed, self._on_secret_changed
        )
        self.framework.observe(
            self.charm.on.secret_rotate, self._on_secret_rotate
        )
        self.framework.observe(
            self.charm.on.secret_remove, self._on_secret_remove
        )
        return ops_svc

    def _hash_ops(self, ops: list) -> str:
        """Hash ops request."""
        return hashlib.sha256(json.dumps(ops).encode()).hexdigest()

    @property
    def label(self) -> str:
        """Secret label to share over keystone resource relation."""
        return self.CREDENTIALS_SECRET_PREFIX + self.username

    @property
    def config_label(self) -> str:
        """Secret label to template configuration from."""
        return self.CONFIGURE_SECRET_PREFIX + self.username

    @property
    def _create_user_tag(self) -> str:
        return "create_user_" + self.username

    @property
    def _delete_user_tag(self) -> str:
        return "delete_user_" + self.username

    def random_string(self, length: int) -> str:
        """Utility function to generate secure random string."""
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for i in range(length))

    def _ensure_credentials(self, refresh_user: bool = False) -> str:
        credentials_id = self.charm.leader_get(self.label)
        suffix_length = 6
        password_length = 18

        if credentials_id:
            if refresh_user:
                username = self.username
                if self.add_suffix:
                    suffix = self.random_string(suffix_length)
                    username += "-" + suffix
                secret = self.model.get_secret(id=credentials_id)
                secret.set_content(
                    {
                        "username": username,
                        "password": self.random_string(password_length),
                    }
                )
            return credentials_id

        username = self.username
        password = self.random_string(password_length)
        if self.add_suffix:
            suffix = self.random_string(suffix_length)
            username += "-" + suffix
        secret = self.model.app.add_secret(
            {"username": username, "password": password},
            label=self.label,
            rotate=self.rotate,
        )
        if not secret.id:
            # We just created the secret, therefore id is always set
            raise RuntimeError("Secret id not set")
        self.charm.leader_set({self.label: secret.id})
        return secret.id

    def _grant_ops_secret(self, relation: ops.Relation):
        secret = self.model.get_secret(id=self._ensure_credentials())
        secret.grant(relation)

    def _get_credentials(self) -> tuple[str, str]:
        credentials_id = self._ensure_credentials()
        secret = self.model.get_secret(id=credentials_id)
        content = secret.get_content(refresh=True)
        return content["username"], content["password"]

    def get_config_credentials(self) -> tuple[str, str] | None:
        """Get credential from config secret."""
        credentials_id = self.charm.leader_get(self.config_label)
        if not credentials_id:
            return None
        secret = self.model.get_secret(id=credentials_id)
        content = secret.get_content(refresh=True)
        return content["username"], content["password"]

    def _update_config_credentials(self) -> bool:
        """Update config credentials.

        Returns True if credentials are updated, False otherwise.
        """
        credentials_id = self.charm.leader_get(self.config_label)
        username, password = self._get_credentials()
        content = {"username": username, "password": password}
        if credentials_id is None:
            secret = self.model.app.add_secret(
                content, label=self.config_label
            )
            if not secret.id:
                # We just created the secret, therefore id is always set
                raise RuntimeError("Secret id not set")
            self.charm.leader_set({self.config_label: secret.id})
            return True

        secret = self.model.get_secret(id=credentials_id)
        old_content = secret.get_content(refresh=True)
        if old_content != content:
            secret.set_content(content)
            return True
        return False

    def _create_user_request(self) -> dict:
        credentials_id = self._ensure_credentials()
        username, _ = self._get_credentials()
        requests = []
        domain = self._params["domain"]
        create_domain = {
            "name": "create_domain",
            "params": {"name": domain, "enable": True},
        }
        requests.append(create_domain)
        if self.role:
            create_role = {
                "name": "create_role",
                "params": {"name": self.role},
            }
            requests.append(create_role)
        params = self._params.copy()
        params.pop("name", None)
        create_user = {
            "name": "create_user",
            "params": {
                "name": username,
                "password": credentials_id,
                **params,
            },
        }
        requests.append(create_user)
        requests.extend(self._create_role_requests(username, domain))
        if self.extra_ops:
            for extra_op in self.extra_ops:
                if isinstance(extra_op, dict):
                    requests.append(extra_op)
                elif callable(extra_op):
                    requests.append(extra_op())
                else:
                    logger.debug(f"Invalid type of extra_op: {extra_op!r}")

        request = {
            "id": self._hash_ops(requests),
            "tag": self._create_user_tag,
            "ops": requests,
        }
        return request

    def _create_role_requests(
        self, username, domain: str | None
    ) -> list[dict]:
        requests = []
        if self.role:
            params = {
                "role": self.role,
            }
            if domain:
                params["domain"] = domain
                params["user_domain"] = domain
            project_domain = self._params.get("project_domain")
            if project_domain:
                params["project_domain"] = project_domain
            params["user"] = username
            grant_role_domain = {"name": "grant_role", "params": params}
            requests.append(grant_role_domain)
            project = self._params.get("project")
            if project:
                requests.append(
                    {
                        "name": "show_project",
                        "params": {
                            "name": project,
                            "domain": project_domain or domain,
                        },
                    }
                )
                params = {
                    "project": "{{ show_project[0].id }}",
                    "role": "{{ create_role[0].id }}",
                    "user": "{{ create_user[0].id }}",
                    "user_domain": "{{ create_domain[0].id }}",
                }
                if project_domain:
                    params["project_domain"] = (
                        "{{ show_project[0].domain_id }}"
                    )
                requests.append(
                    {
                        "name": "grant_role",
                        "params": params,
                    }
                )
        return requests

    def _delete_user_request(self, users: list[str]) -> dict:
        requests = []
        for user in users:
            params = {"name": user}
            domain = self._params.get("domain")
            if domain:
                params["domain"] = domain
            requests.append(
                {
                    "name": "delete_user",
                    "params": params,
                }
            )

        return {
            "id": self._hash_ops(requests),
            "tag": self._delete_user_tag,
            "ops": requests,
        }

    def _process_create_user_response(self, response: dict) -> None:
        if {op.get("return-code") for op in response.get("ops", [])} == {0}:
            logger.debug("Create user completed.")
            config_credentials = self.get_config_credentials()
            credentials_updated = self._update_config_credentials()
            if config_credentials and credentials_updated:
                username = config_credentials[0]
                self.add_user_to_delete_user_list(username)
        else:
            logger.debug("Error in creation of user ops " f"{response}")

    def add_user_to_delete_user_list(self, user: str) -> None:
        """Update users list to delete."""
        logger.debug(f"Adding user to delete list {user}")
        old_users = self.charm.leader_get("old_users")
        delete_users = json.loads(old_users) if old_users else []
        if user not in delete_users:
            delete_users.append(user)
            self.charm.leader_set({"old_users": json.dumps(delete_users)})

    def _process_delete_user_response(self, response: dict) -> None:
        deleted_users = []
        for op in response.get("ops", []):
            if op.get("return-code") == 0:
                deleted_users.append(op.get("value").get("name"))
            else:
                logger.debug(f"Error in running delete user for op {op}")

        if deleted_users:
            logger.debug(f"Deleted users: {deleted_users}")

        old_users = self.charm.leader_get("old_users")
        users_to_delete = json.loads(old_users) if old_users else []
        new_users_to_delete = [
            x for x in users_to_delete if x not in deleted_users
        ]
        self.charm.leader_set({"old_users": json.dumps(new_users_to_delete)})

    def _on_secret_changed(self, event: ops.SecretChangedEvent):
        logger.debug(
            f"secret-changed triggered for label {event.secret.label}"
        )

        # Secret change on configured user secret
        if event.secret.label == self.config_label:
            logger.debug(
                "Calling configure charm to populate user info in "
                "configuration files"
            )
            self.callback_f(event)
        else:
            logger.debug(
                "Ignoring the secret-changed event for label "
                f"{event.secret.label}"
            )

    def _on_secret_rotate(self, event: ops.SecretRotateEvent):
        # All the juju secrets are created on leader unit, so return
        # if unit is not leader at this stage instead of checking at
        # each secret.
        logger.debug(f"secret-rotate triggered for label {event.secret.label}")
        if not self.model.unit.is_leader():
            logger.debug("Not leader unit, no action required")
            return

        # Secret rotate on stack user secret sent to ops
        if event.secret.label == self.label:
            self._ensure_credentials(refresh_user=True)
            request = self._create_user_request()
            logger.debug(f"Sending ops request: {request}")
            self.interface.request_ops(request)
        else:
            logger.debug(
                "Ignoring the secret-rotate event for label "
                f"{event.secret.label}"
            )

    def _on_secret_remove(self, event: ops.SecretRemoveEvent):
        logger.debug(f"secret-remove triggered for label {event.secret.label}")
        if not self.model.unit.is_leader():
            logger.debug("Not leader unit, no action required")
            return

        # Secret remove on configured stack admin secret
        if event.secret.label == self.config_label:
            old_users = self.charm.leader_get("old_users")
            users_to_delete = json.loads(old_users) if old_users else []

            if not users_to_delete:
                return

            request = self._delete_user_request(users_to_delete)
            logger.debug(f"Sending ops request: {request}")
            self.interface.request_ops(request)
        else:
            logger.debug(
                "Ignoring the secret-remove event for label "
                f"{event.secret.label}"
            )

    def _on_provider_ready(self, event) -> None:
        """Handles response available  events."""
        logger.info("Handle response from identity ops")
        if not self.model.unit.is_leader():
            return
        self.interface.request_ops(self._create_user_request())
        self._grant_ops_secret(event.relation)
        self.callback_f(event)

    def _on_response_available(self, event) -> None:
        """Handles response available  events."""
        if not self.model.unit.is_leader():
            return
        logger.info("Handle response from identity ops")

        response = self.interface.response
        tag = response.get("tag")
        if tag == self._create_user_tag:
            self._process_create_user_response(response)
            if self.extra_ops_process is not None:
                self.extra_ops_process(event, response)
        elif tag == self._delete_user_tag:
            self._process_delete_user_response(response)
        self.callback_f(event)

    def _on_provider_goneaway(self, event) -> None:
        """Handle gone_away  event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether the relation is ready."""
        return self.get_config_credentials() is not None


@sunbeam_tracing.trace_type
class CertificateTransferRequiresHandler(RelationHandler):
    """Handle certificate transfer relation on the requires side."""

    interface: "certificate_transfer.CertificateTransferRequires"

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for tls relation."""
        logger.debug("Setting up certificate transfer event handler")

        from charms.certificate_transfer_interface.v0.certificate_transfer import (
            CertificateTransferRequires,
        )

        recv_ca_cert = sunbeam_tracing.trace_type(CertificateTransferRequires)(
            self.charm, "receive-ca-cert"
        )
        self.framework.observe(
            recv_ca_cert.on.certificate_available,
            self._on_recv_ca_cert_available,
        )
        self.framework.observe(
            recv_ca_cert.on.certificate_removed, self._on_recv_ca_cert_removed
        )
        return recv_ca_cert

    def _on_recv_ca_cert_available(self, event: ops.framework.EventBase):
        self.callback_f(event)

    def _on_recv_ca_cert_removed(self, event: ops.framework.EventBase):
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check if relation handler is ready."""
        return True

    def context(self) -> dict:
        """Context containing ca cert data."""
        receive_ca_cert_relations = list(
            self.model.relations[self.relation_name]
        )
        if not receive_ca_cert_relations:
            return {}

        ca_bundle = []
        for k, v in receive_ca_cert_relations[0].data.items():
            if isinstance(k, Unit) and k != self.model.unit:
                ca = v.get("ca")
                chain = json.loads(v.get("chain", "[]"))
                if ca and ca not in ca_bundle:
                    ca_bundle.append(ca)
                for chain_ in chain:
                    if chain_ not in ca_bundle:
                        ca_bundle.append(chain_)

        return {"ca_bundle": "\n".join(ca_bundle)}


@sunbeam_tracing.trace_type
class TraefikRouteHandler(RelationHandler):
    """Base class to handle traefik route relations."""

    interface: "traefik_route.TraefikRouteRequirer"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = False,
        ingress_names: list | None = None,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, relation_name, callback_f, mandatory)
        self.ingress_names = ingress_names or []

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an Ingress relation."""
        logger.debug("Setting up ingress event handler")
        from charms.traefik_k8s.v0.traefik_route import (
            TraefikRouteRequirer,
        )

        interface = sunbeam_tracing.trace_type(TraefikRouteRequirer)(
            self.charm,
            self.model.get_relation(self.relation_name),  # type: ignore # TraefikRouteRequirer has safeguards against None
            self.relation_name,
            # note(gboutry): raw=True until we rework the TLS in requirer charms
            raw=True,
        )

        self.framework.observe(interface.on.ready, self._on_ingress_ready)
        self.framework.observe(
            self.charm.on[self.relation_name].relation_joined,
            self._on_traefik_relation_joined,
        )
        return interface

    def _on_traefik_relation_joined(
        self, event: ops.charm.RelationEvent
    ) -> None:
        """Handle traefik relation joined event."""
        # This is passed as None during the init method, so update the
        # relation attribute in TraefikRouteRequirer
        self.interface._relation = event.relation

    def _on_ingress_ready(self, event: ops.charm.RelationEvent) -> None:
        """Handle ingress relation changed events.

        `event` is an instance of
        `charms.traefik_k8s.v2.ingress.IngressPerAppReadyEvent`.
        """
        if self.interface.is_ready():
            self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether the handler is ready for use."""
        if self.charm.unit.is_leader():
            try:
                return bool(self.interface.external_host)
            except ModelError:
                return False
        else:
            return self.interface.is_ready()

    def context(self) -> dict:
        """Context containing ingress data.

        Returns dictionary of ingress_key: value
        ingress_key will be <ingress name>_ingress_path (replace - with _ in name)
        value will be /<model name>-<ingress name>
        """
        return {
            f"{name.replace('-', '_')}_ingress_path": f"/{self.charm.model.name}-{name}"
            for name in self.ingress_names
        }


@sunbeam_tracing.trace_type
class NovaServiceRequiresHandler(RelationHandler):
    """Handle nova service relation on the requires side."""

    interface: "nova_service.NovaServiceRequires"

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for Nova service relation."""
        import charms.nova_k8s.v0.nova_service as nova_svc

        logger.debug("Setting up Nova service event handler")
        svc = sunbeam_tracing.trace_type(nova_svc.NovaServiceRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_changed,
            self._on_config_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_goneaway,
        )
        return svc

    def _on_config_changed(self, event: ops.framework.EventBase) -> None:
        """Handle config_changed  event."""
        logger.debug("Nova service provider config changed event received")
        self.callback_f(event)

    def _on_goneaway(self, event: ops.framework.EventBase) -> None:
        """Handle gone_away  event."""
        logger.debug("Nova service relation is departed/broken")
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        try:
            return bool(self.interface.nova_spiceproxy_url)
        except (AttributeError, KeyError):
            return False


@sunbeam_tracing.trace_type
class LogForwardHandler(RelationHandler):
    """Handle log forward relation on the requires side."""

    interface: "loki_push_api.LogForwarder"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        mandatory: bool = False,
    ):
        """Create a new log-forward handler.

        Create a new LogForwardHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param mandatory: If the relation is mandatory to proceed with
                          configuring charm
        :type mandatory: bool
        """
        super().__init__(charm, relation_name, lambda *args: None, mandatory)

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for log forward relation."""
        import charms.loki_k8s.v1.loki_push_api as loki_push_api

        logger.debug("Setting up log forward event handler")
        log_forwarder = sunbeam_tracing.trace_type(loki_push_api.LogForwarder)(
            self.charm,
            relation_name=self.relation_name,
        )
        return log_forwarder

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return self.interface.is_ready()


@sunbeam_tracing.trace_type
class TracingRequireHandler(RelationHandler):
    """Handle tracing relation on the requires side."""

    interface: "tracing.TracingEndpointRequirer"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        mandatory: bool = False,
        protocols: list[str] | None = None,
    ) -> None:
        """Create a new tracing-relation handler.

        :param charm: the Charm class the handler
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param mandatory: If the relation is mandatory to proceed with
                          configuring charm.
        :type mandatory: bool
        """
        super().__init__(charm, relation_name, lambda *args: None, mandatory)
        if protocols is None:
            protocols = ["otlp_http"]
        self.protocols = protocols

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for tracing relation."""
        import charms.tempo_k8s.v2.tracing as tracing

        tracing_interface = sunbeam_tracing.trace_type(
            tracing.TracingEndpointRequirer
        )(
            self.charm,
            self.relation_name,
            protocols=self.protocols,  # type: ignore[arg-type]
        )

        return tracing_interface

    def tracing_endpoint(self) -> str | None:
        """Otlp endpoint for charm tracing."""
        if self.ready:
            return self.interface.get_endpoint("otlp_http")
        return None

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return self.interface.is_ready()


@sunbeam_tracing.trace_type
class GnocchiServiceRequiresHandler(RelationHandler):
    """Handle gnocchi service relation on the requires side."""

    interface: "gnocchi_service.GnocchiServiceRequires"

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for Gnocchi service relation."""
        import charms.gnocchi_k8s.v0.gnocchi_service as gnocchi_svc

        logger.debug("Setting up Gnocchi service event handler")
        svc = sunbeam_tracing.trace_type(gnocchi_svc.GnocchiServiceRequires)(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.readiness_changed,
            self._on_gnocchi_service_readiness_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_gnocchi_service_goneaway,
        )
        return svc

    def _on_gnocchi_service_readiness_changed(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle config_changed  event."""
        logger.debug("Gnocchi service readiness changed event received")
        self.callback_f(event)

    def _on_gnocchi_service_goneaway(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle gone_away  event."""
        logger.debug("Gnocchi service gone away event received")
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return self.interface.service_ready


@sunbeam_tracing.trace_type
class ServiceReadinessRequiresHandler(RelationHandler):
    """Handle service-ready relation on the requires side."""

    interface: "service_readiness.ServiceReadinessRequirer"

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for service-ready relation."""
        import charms.sunbeam_libs.v0.service_readiness as service_readiness

        logger.debug(
            f"Setting up service-ready event handler for {self.relation_name}"
        )
        svc = sunbeam_tracing.trace_type(
            service_readiness.ServiceReadinessRequirer
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.readiness_changed,
            self._on_remote_service_readiness_changed,
        )
        self.framework.observe(
            svc.on.goneaway,
            self._on_remote_service_goneaway,
        )
        return svc

    def _on_remote_service_readiness_changed(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle config_changed  event."""
        logger.debug(
            f"Remote service readiness changed event received for relation {self.relation_name}"
        )
        self.callback_f(event)

    def _on_remote_service_goneaway(
        self, event: ops.framework.EventBase
    ) -> None:
        """Handle gone_away  event."""
        logger.debug(
            "Remote service gone away event received for relation {self.relation_name}"
        )
        self.callback_f(event)
        if self.mandatory:
            self.status.set(BlockedStatus("integration missing"))

    @property
    def ready(self) -> bool:
        """Whether handler is ready for use."""
        return self.interface.service_ready


@sunbeam_tracing.trace_type
class ServiceReadinessProviderHandler(RelationHandler):
    """Handler for service-readiness relation on provider side."""

    interface: "service_readiness.ServiceReadinessProvider"

    def setup_event_handler(self):
        """Configure event handlers for service-readiness relation."""
        import charms.sunbeam_libs.v0.service_readiness as service_readiness

        logger.debug(f"Setting up event handler for {self.relation_name}")

        svc = sunbeam_tracing.trace_type(
            service_readiness.ServiceReadinessProvider
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.service_readiness,
            self._on_service_readiness,
        )
        return svc

    def _on_service_readiness(self, event: ops.framework.EventBase) -> None:
        """Handle service readiness request event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


@sunbeam_tracing.trace_type
class CinderVolumeRequiresHandler(RelationHandler):
    """Handler for Cinder Volume relation."""

    interface: "sunbeam_cinder_volume.CinderVolumeRequires"

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        relation_name: str,
        backend_key: str,
        callback_f: Callable,
        mandatory: bool = True,
    ):
        self.backend_key = backend_key
        super().__init__(charm, relation_name, callback_f, mandatory=mandatory)

    def setup_event_handler(self):
        """Configure event handlers for Cinder Volume relation."""
        import charms.cinder_volume.v0.cinder_volume as sunbeam_cinder_volume

        logger.debug("Setting up Cinder Volume event handler")
        cinder_volume = sunbeam_tracing.trace_type(
            sunbeam_cinder_volume.CinderVolumeRequires
        )(
            self.charm,
            self.relation_name,
            backend_key=self.backend_key,
        )
        self.framework.observe(
            cinder_volume.on.ready,
            self._on_cinder_volume_ready,
        )

        return cinder_volume

    def _on_cinder_volume_ready(self, event: ops.RelationEvent) -> None:
        """Handles Cinder Volume change events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return self.interface.provider_ready()

    def snap(self) -> str | None:
        """Return snap name."""
        return self.interface.snap_name()


@sunbeam_tracing.trace_type
class TrustedDashboardProvidesHandler(RelationHandler):
    """Handler for identity service relation."""

    interface: "trusted_dashboard.TrustedDashboardProvider"

    def setup_event_handler(self):
        """Configure event handlers for the trusted dashboard relation."""
        logger.debug("Setting up trusted dashboard event handler")
        import charms.horizon_k8s.v0.trusted_dashboard as trusted_dashboard

        dashboard = trusted_dashboard.TrustedDashboardProvider(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            dashboard.on.providers_changed,
            self._on_providers_changed,
        )
        return dashboard

    def set_provider_info(self, trusted_dashboard: str) -> None:
        """Set the provider information for the trusted dashboard."""
        self.interface.set_provider_info(trusted_dashboard)

    def _on_providers_changed(self, event) -> None:
        if self.interface.fid_providers:
            self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True

    def context(self) -> dict:
        """Return context for the trusted dashboard relation."""
        return {
            "fid_providers": self.interface.fid_providers,
        }
