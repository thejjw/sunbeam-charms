#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
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

"""Nova Operator Charm.

This charm provide Nova services as part of an OpenStack deployment
"""

import logging
import socket
import uuid
from typing import (
    Callable,
    List,
    Mapping,
)

import charms.sunbeam_nova_compute_operator.v0.cloud_compute as cloud_compute
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from charms.nova_k8s.v0.nova_service import (
    NovaConfigRequestEvent,
    NovaServiceProvides,
)
from ops.charm import (
    CharmBase,
)
from ops.main import (
    main,
)
from ops.pebble import (
    ExecError,
)

logger = logging.getLogger(__name__)

NOVA_WSGI_CONTAINER = "nova-api"
NOVA_SCHEDULER_CONTAINER = "nova-scheduler"
NOVA_CONDUCTOR_CONTAINER = "nova-conductor"
NOVA_SPICEPROXY_CONTAINER = "nova-spiceproxy"
NOVA_API_INGRESS_NAME = "nova"
NOVA_SPICEPROXY_INGRESS_NAME = "nova-spiceproxy"
NOVA_SPICEPROXY_INGRESS_PORT = 6182


class WSGINovaMetadataConfigContext(sunbeam_ctxts.ConfigContext):
    """Configuration context for WSGI configuration."""

    def context(self) -> dict:
        """WSGI configuration options."""
        return {
            "name": self.charm.service_name,
            "public_port": 8775,
            "user": self.charm.service_user,
            "group": self.charm.service_group,
            "wsgi_admin_script": "/usr/bin/nova-metadata-wsgi",
            "wsgi_public_script": "/usr/bin/nova-metadata-wsgi",
            "error_log": "/dev/stdout",
            "custom_log": "/dev/stdout",
        }


class NovaSchedulerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Nova scheduler."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Nova Scheduler service layer.

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "nova scheduler layer",
            "description": "pebble configuration for nova services",
            "services": {
                "nova-scheduler": {
                    "override": "replace",
                    "summary": "Nova Scheduler",
                    "command": "nova-scheduler",
                    "user": "nova",
                    "group": "nova",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "nova",
                0o640,
            ),
        ]

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logging.debug("Service checks enabled for nova scheduler")
            return super().service_ready
        else:
            logging.debug("Service checks disabled for nova scheduler")
            return self.pebble_ready


class NovaConductorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Nova Conductor container."""

    def get_layer(self):
        """Nova Conductor service.

        :returns: pebble service layer configuration for conductor service
        :rtype: dict
        """
        return {
            "summary": "nova conductor layer",
            "description": "pebble configuration for nova services",
            "services": {
                "nova-conductor": {
                    "override": "replace",
                    "summary": "Nova Conductor",
                    "command": "nova-conductor",
                    "user": "nova",
                    "group": "nova",
                }
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "nova",
                0o640,
            ),
        ]


class NovaSpiceProxyPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for Nova spice proxy."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enable_service_check = True

    def get_layer(self) -> dict:
        """Nova Scheduler service layer.

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "nova spice proxy layer",
            "description": "pebble configuration for nova services",
            "services": {
                "nova-spiceproxy": {
                    "override": "replace",
                    "summary": "Nova Spice Proxy",
                    "command": "nova-spicehtml5proxy",
                    "user": "nova",
                    "group": "nova",
                },
                "apache forwarder": {
                    "override": "replace",
                    "summary": "apache",
                    "command": "/usr/sbin/apache2ctl -DFOREGROUND",
                },
            },
        }

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/apache2/sites-enabled/nova-spiceproxy-forwarding.conf",
                self.charm.service_user,
                self.charm.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "nova",
                0o640,
            ),
        ]

    @property
    def service_ready(self) -> bool:
        """Determine whether the service the container provides is running."""
        if self.enable_service_check:
            logging.debug("Service checks enabled for nova spice proxy")
            return super().service_ready
        else:
            logging.debug("Service checks disabled for nova spice proxy")
            return self.pebble_ready


class CloudComputeRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Handles the cloud-compute relation on the requires side."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        region: str,
        callback_f: Callable,
        mandatory: bool = False,
    ):
        """Constructor for CloudComputeRequiresHandler.

        Creates a new CloudComputeRequiresHandler that handles initial
        events from the relation and invokes the provided callbacks based on
        the event raised.

        :param charm: the Charm class the handler is for
        :type charm: ops.charm.CharmBase
        :param relation_name: the relation the handler is bound to
        :type relation_name: str
        :param region: the region the nova services are configured for
        :type region: str
        :param callback_f: the function to call when the nodes are connected
        :type callback_f: Callable
        :param mandatory: flag to determine if relation handler is mandatory
        :type mandatory: bool
        """
        self.region = region
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self):
        """Configure event handlers for the cloud-compute service relation."""
        logger.debug("Setting up cloud-compute event handler")
        compute_service = cloud_compute.CloudComputeRequires(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            compute_service.on.compute_nodes_connected,
            self._compute_nodes_connected,
        )
        self.framework.observe(
            compute_service.on.compute_nodes_ready,
            self._compute_nodes_connected,
        )
        return compute_service

    def _compute_nodes_connected(self, event) -> None:
        """Handles cloud-compute change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by an availability zone)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Interface ready for use."""
        return True


class NovaServiceProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for nova service relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for nova service relation."""
        logger.debug("Setting up Nova service event handler")
        svc = NovaServiceProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            svc.on.config_request,
            self._on_config_request,
        )
        return svc

    def _on_config_request(self, event: NovaConfigRequestEvent) -> None:
        """Handle Config request event."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


class NovaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "nova-api"
    wsgi_admin_script = "/usr/bin/nova-api-wsgi"
    wsgi_public_script = "/usr/bin/nova-api-wsgi"
    shared_metadata_secret_key = "shared-metadata-secret"
    mandatory_relations = {
        "database",
        "api-database",
        "cell-database",
        "amqp",
        "identity-service",
        "traefik-route-public",
    }

    def __init__(self, framework):
        self.traefik_route_public = None
        self.traefik_route_internal = None
        super().__init__(framework)
        self.framework.observe(
            self.on.peers_relation_created, self._on_peer_relation_created
        )
        self.framework.observe(
            self.on["peers"].relation_departed, self._on_peer_relation_departed
        )

    def _on_peer_relation_created(
        self, event: ops.framework.EventBase
    ) -> None:
        logger.info("Setting peer unit data")
        self.peers.set_unit_data({"host": socket.getfqdn()})

    def _on_peer_relation_departed(
        self, event: ops.framework.EventBase
    ) -> None:
        self.handle_traefik_ready(event)

    @property
    def db_sync_cmds(self) -> List[List[str]]:
        """DB sync commands for Nova operator."""
        # we must provide the database connection for the cell database,
        # because the database credentials are different to the main database.
        # If we don't provide them:
        # > If you don't specify --database_connection then nova-manage will
        # > use the [database]/connection value from your config file,
        # > and mangle the database name to have a _cell0 suffix.
        # https://docs.openstack.org/nova/yoga/admin/cells.html#configuring-a-new-deployment
        cell_database = self.dbs["cell-database"].context()["connection"]
        return [
            ["sudo", "-u", "nova", "nova-manage", "api_db", "sync"],
            [
                "sudo",
                "-u",
                "nova",
                "nova-manage",
                "cell_v2",
                "map_cell0",
                "--database_connection",
                cell_database,
            ],
            ["sudo", "-u", "nova", "nova-manage", "db", "sync"],
            ["/root/cell_create_wrapper.sh", "cell1"],
        ]

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/nova/nova.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "nova"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "nova"

    @property
    def service_endpoints(self):
        """Service endpoints for Nova."""
        return [
            {
                "service_name": "nova",
                "type": "compute",
                "description": "OpenStack Compute",
                "internal_url": f"{self.internal_url}/v2.1",
                "public_url": f"{self.public_url}/v2.1",
                "admin_url": f"{self.admin_url}/v2.1",
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Default port for service ingress."""
        return 8774

    @property
    def public_url(self) -> str:
        """Url for accessing the public endpoint for nova service."""
        if self.traefik_route_public and self.traefik_route_public.ready:
            scheme = self.traefik_route_public.interface.scheme
            external_host = self.traefik_route_public.interface.external_host
            public_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{NOVA_API_INGRESS_NAME}"
            )
            return self.add_explicit_port(public_url)
        else:
            return self.add_explicit_port(
                self.service_url(self.public_ingress_address)
            )

    @property
    def internal_url(self) -> str:
        """Url for accessing the internal endpoint for nova service."""
        if self.traefik_route_internal and self.traefik_route_internal.ready:
            scheme = self.traefik_route_internal.interface.scheme
            external_host = self.traefik_route_internal.interface.external_host
            internal_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{NOVA_API_INGRESS_NAME}"
            )
            return self.add_explicit_port(internal_url)
        else:
            return self.admin_url

    @property
    def nova_spiceproxy_public_url(self) -> str | None:
        """URL for accessing public endpoint for nova spiceproxy service."""
        if self.traefik_route_public and self.traefik_route_public.ready:
            scheme = self.traefik_route_public.interface.scheme
            external_host = self.traefik_route_public.interface.external_host
            public_url = (
                f"{scheme}://{external_host}/{self.model.name}"
                f"-{NOVA_SPICEPROXY_INGRESS_NAME}"
            )
            return public_url

        return None

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Need to override the default
        because we're registering multiple databases.
        """
        return {
            "database": "nova",
            "api-database": "nova_api",
            "cell-database": "nova_cell0",
        }

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.ServicePebbleHandler]:
        """Pebble handlers for operator."""
        pebble_handlers = [
            sunbeam_chandlers.WSGIPebbleHandler(
                self,
                NOVA_WSGI_CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            ),
            NovaSchedulerPebbleHandler(
                self,
                NOVA_SCHEDULER_CONTAINER,
                "nova-scheduler",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            NovaConductorPebbleHandler(
                self,
                NOVA_CONDUCTOR_CONTAINER,
                "nova-conductor",
                [],
                self.template_dir,
                self.configure_charm,
            ),
            NovaSpiceProxyPebbleHandler(
                self,
                NOVA_SPICEPROXY_CONTAINER,
                "nova-spiceproxy",
                [],
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for operator."""
        handlers = super().get_relation_handlers(handlers or [])
        if self.can_add_handler("cloud-compute", handlers):
            self.compute_nodes = CloudComputeRequiresHandler(
                self,
                "cloud-compute",
                self.model.config["region"],
                self.register_compute_nodes,
            )
            handlers.append(self.compute_nodes)

        if self.can_add_handler("nova-service", handlers):
            self.config_svc = NovaServiceProvidesHandler(
                self,
                "nova-service",
                self.set_config_from_event,
            )
            handlers.append(self.config_svc)

        self.traefik_route_public = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-public",
            self.handle_traefik_ready,
            "traefik-route-public" in self.mandatory_relations,
            [NOVA_API_INGRESS_NAME, NOVA_SPICEPROXY_INGRESS_NAME],
        )
        handlers.append(self.traefik_route_public)
        self.traefik_route_internal = sunbeam_rhandlers.TraefikRouteHandler(
            self,
            "traefik-route-internal",
            self.handle_traefik_ready,
            "traefik-route-internal" in self.mandatory_relations,
            [NOVA_API_INGRESS_NAME, NOVA_SPICEPROXY_INGRESS_NAME],
        )
        handlers.append(self.traefik_route_internal)

        return handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend(
            [
                WSGINovaMetadataConfigContext(
                    self,
                    "wsgi_nova_metadata",
                )
            ]
        )
        return _cadapters

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the service."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                "/etc/nova/nova.conf",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/usr/local/share/ca-certificates/ca-bundle.pem",
                "root",
                "nova",
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/root/cell_create_wrapper.sh", "root", "root", 0o755
            ),
        ]
        return _cconfigs

    @property
    def traefik_config(self) -> dict:
        """Config to publish to traefik."""
        model = self.model.name
        router_cfg = {}
        # Add routers for both nova-api and nova-spiceproxy
        for app in NOVA_API_INGRESS_NAME, NOVA_SPICEPROXY_INGRESS_NAME:
            router_cfg.update(
                {
                    f"juju-{model}-{app}-router": {
                        "rule": f"PathPrefix(`/{model}-{app}`)",
                        "service": f"juju-{model}-{app}-service",
                        "entryPoints": ["web"],
                    },
                    f"juju-{model}-{app}-router-tls": {
                        "rule": f"PathPrefix(`/{model}-{app}`)",
                        "service": f"juju-{model}-{app}-service",
                        "entryPoints": ["websecure"],
                        "tls": {},
                    },
                }
            )

        # Get host key value from all units
        hosts = self.peers.get_all_unit_values(
            key="host", include_local_unit=True
        )
        api_lb_servers = [
            {"url": f"http://{host}:{self.default_public_ingress_port}"}
            for host in hosts
        ]
        spice_lb_servers = [
            {"url": f"http://{host}:{NOVA_SPICEPROXY_INGRESS_PORT}"}
            for host in hosts
        ]
        # Add services for heat-api and heat-api-cfn
        service_cfg = {
            f"juju-{model}-{NOVA_API_INGRESS_NAME}-service": {
                "loadBalancer": {"servers": api_lb_servers},
            },
            f"juju-{model}-{NOVA_SPICEPROXY_INGRESS_NAME}-service": {
                "loadBalancer": {"servers": spice_lb_servers},
            },
        }

        config = {
            "http": {
                "routers": router_cfg,
                "services": service_cfg,
            },
        }
        return config

    def get_shared_metadatasecret(self):
        """Return the shared metadata secret."""
        return self.leader_get(self.shared_metadata_secret_key)

    def set_shared_metadatasecret(self):
        """Store the shared metadata secret."""
        self.leader_set({self.shared_metadata_secret_key: str(uuid.uuid1())})

    def register_compute_nodes(self, event: ops.framework.EventBase) -> None:
        """Register compute nodes when the event is received.

        :param event: the event that new compute nodes are available.
        :type event: ops.framework.EventBase
        :return: None
        """
        logger.debug("register_compute_nodes event received")
        if not self.bootstrapped():
            logger.debug("Event received while not bootstrapped, deferring")
            event.defer()
            return

        if not self.unit.is_leader():
            logger.debug("Unit is not the current leader")
            return

        handler = self.get_named_pebble_handler(NOVA_CONDUCTOR_CONTAINER)
        # TODO(wolsen) make sure the container is there to run the command in
        # if not handler.service_ready:
        #     logger.info(f'Container {NOVA_CONDUCTOR_CONTAINER} is not ready,'
        #                 ' deferring')
        #     event.defer()
        #     return

        self.compute_nodes.interface.set_controller_info(
            region=self.model.config["region"],
            cross_az_attach=False,
        )

        try:
            logger.debug("Discovering hosts for cell1")
            cell1_uuid = self.get_cell_uuid("cell1")
            cmd = [
                "nova-manage",
                "cell_v2",
                "discover_hosts",
                "--cell_uuid",
                cell1_uuid,
                "--verbose",
            ]
            handler.execute(cmd, exception_on_error=True)
        except ExecError:
            logger.exception("Failed to discover hosts for cell1")
            raise

    def _update_service_endpoints(self):
        try:
            if self.id_svc.update_service_endpoints:
                logger.info(
                    "Updating service endpoints after ingress relation changed"
                )
                self.id_svc.update_service_endpoints(self.service_endpoints)
        except (AttributeError, KeyError):
            pass

    def handle_traefik_ready(self, event: ops.framework.EventBase):
        """Handle Traefik route ready callback."""
        if not self.unit.is_leader():
            logger.debug(
                "Not a leader unit, not updating traefik route config"
            )
            return

        if self.traefik_route_public:
            logger.debug("Sending traefik config for public interface")
            self.traefik_route_public.interface.submit_to_traefik(
                config=self.traefik_config
            )

            if self.traefik_route_public.ready:
                self._update_service_endpoints()

        if self.traefik_route_internal:
            logger.debug("Sending traefik config for internal interface")
            self.traefik_route_internal.interface.submit_to_traefik(
                config=self.traefik_config
            )

            if self.traefik_route_internal.ready:
                self._update_service_endpoints()

    def get_cell_uuid(self, cell, fatal=True):
        """Returns the cell UUID from the name.

        :param cell: string cell name i.e. 'cell1'
        :returns: string cell uuid
        """
        logger.debug(f"listing cells for {cell}")
        cells = self.get_cells()
        cell_info = cells.get(cell)
        if not cell_info:
            if fatal:
                raise Exception(f"Cell {cell} not found")
            return None

        return cell_info["uuid"]

    def get_cells(self):
        """Returns the cells configured in the environment.

        :returns: dict containing the cell information
        :rtype: dict
        """
        logger.info("Getting details of cells")
        cells = {}
        cmd = ["sudo", "nova-manage", "cell_v2", "list_cells", "--verbose"]
        handler = self.get_named_pebble_handler(NOVA_CONDUCTOR_CONTAINER)
        try:
            out = handler.execute(cmd, exception_on_error=True)
        except ExecError:
            logger.exception("list_cells failed")
            raise

        for line in out.split("\n"):
            columns = line.split("|")
            if len(columns) < 2:
                continue
            columns = [c.strip() for c in columns]
            try:
                uuid.UUID(columns[2].strip())
                cells[columns[1]] = {
                    "uuid": columns[2],
                    "amqp": columns[3],
                    "db": columns[4],
                }
            except ValueError:
                pass

        return cells

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Callback handler for nova operator configuration."""
        if not self.peers.ready:
            return

        metadata_secret = self.get_shared_metadatasecret()
        if metadata_secret:
            logger.debug("Found metadata secret in leader DB")
        else:
            if self.unit.is_leader():
                logger.debug("Creating metadata secret")
                self.set_shared_metadatasecret()
                self.handle_traefik_ready(event)
                self.set_config_on_update()
            else:
                logger.debug("Metadata secret not ready")
                return
        # Do not run service check for nova scheduler as it is broken until db
        # migrations have run.
        scheduler_handler = self.get_named_pebble_handler(
            NOVA_SCHEDULER_CONTAINER
        )
        scheduler_handler.enable_service_check = False

        # Enable apache proxy_http module for nova-spiceproxy apache forwarding
        nova_spice_handler = self.get_named_pebble_handler(
            NOVA_SPICEPROXY_CONTAINER
        )
        if nova_spice_handler.pebble_ready:
            nova_spice_handler.execute(
                ["a2enmod", "proxy_http"], exception_on_error=True
            )
            nova_spice_handler.execute(
                ["apt-get", "update"], exception_on_error=True
            )
            nova_spice_handler.execute(
                ["apt", "install", "spice-html5", "-y"],
                exception_on_error=True,
            )

        super().configure_charm(event)
        if scheduler_handler.pebble_ready:
            logging.debug("Starting nova scheduler service, pebble ready")
            # Restart nova-scheduler service after cell1 is created
            # Creation of cell1 is part of bootstrap process
            scheduler_handler.start_all()
            scheduler_handler.enable_service_check = True
        else:
            logging.debug(
                "Not starting nova scheduler service, pebble not ready"
            )

    def set_config_from_event(self, event: ops.framework.EventBase) -> None:
        """Set config in relation data."""
        if self.nova_spiceproxy_public_url:
            self.config_svc.interface.set_config(
                relation=event.relation,
                nova_spiceproxy_url=self.nova_spiceproxy_public_url,
            )
        else:
            logging.debug("Nova spiceproxy not yet set, not sending config")

    def set_config_on_update(self) -> None:
        """Set config on relation on update of local data."""
        if self.nova_spiceproxy_public_url:
            self.config_svc.interface.set_config(
                relation=None,
                nova_spiceproxy_url=self.nova_spiceproxy_public_url,
            )
        else:
            logging.debug("Nova spiceproxy not yet set, not sending config")


if __name__ == "__main__":
    main(NovaOperatorCharm)
