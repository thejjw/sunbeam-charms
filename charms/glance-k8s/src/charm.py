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


"""Glance Operator Charm.

This charm provide Glance services as part of an OpenStack deployment
"""

import logging
from typing import (
    Callable,
    List,
)

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.compound_status as compound_status
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from ops.charm import (
    CharmBase,
)
from ops.framework import (
    EventBase,
    StoredState,
)
from ops.main import (
    main,
)
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    WaitingStatus,
)

logger = logging.getLogger(__name__)

# Use Apache to translate /<model-name> to /.  This should be possible
# adding rules to the api-paste.ini but this does not seem to work
# and glance always interprets the mode-name as a requested version number.


class GlanceAPIPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Handler for glance api container."""

    def get_layer(self) -> dict:
        """Glance API service pebble layer.

        :returns: pebble layer configuration for glance api service
        """
        return {
            "summary": f"{self.service_name} layer",
            "description": "pebble config layer for glance api service",
            "services": {
                f"{self.service_name}": {
                    "override": "replace",
                    "summary": f"{self.service_name} standalone",
                    "command": (
                        "/usr/bin/glance-api "
                        "--config-file /etc/glance/glance-api.conf"
                    ),
                    "startup": "disabled",
                    "user": "glance",
                    "group": "glance",
                },
                "apache forwarder": {
                    "override": "replace",
                    "summary": "apache",
                    "command": "/usr/sbin/apache2ctl -DFOREGROUND",
                    "startup": "disabled",
                },
            },
        }


class GlanceStorageRelationHandler(sunbeam_rhandlers.CephClientHandler):
    """A relation handler for optional glance storage relations.

    This will claim ready if there is local storage that is available in
    order to configure the glance local registry. If there is a ceph
    relation, then this will wait until the ceph relation is fulfilled before
    claiming it is ready.
    """

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
        allow_ec_overwrites: bool = True,
        app_name: str = None,
        juju_storage_name: str = None,
    ) -> None:
        """Run constructor."""
        self.juju_storage_name = juju_storage_name
        super().__init__(
            charm,
            relation_name,
            callback_f,
            allow_ec_overwrites,
            app_name,
            mandatory=True,
        )

    def set_status(self, status: compound_status.Status) -> None:
        """Override the base set_status.

        Custom logic is required here since this relation handler
        falls back to local storage if the ceph relation isn't found.
        """
        if (
            not self.charm.has_ceph_relation()
            and not self.charm.has_local_storage()
        ):
            status.set(
                BlockedStatus(
                    "ceph integration and local storage are not available"
                )
            )
        elif self.ready:
            status.set(ActiveStatus(""))
        else:
            status.set(WaitingStatus("integration incomplete"))

    @property
    def ready(self) -> bool:
        """Determines if the ceph relation is ready or not.

        This relation will be ready in one of the following conditions:
         * If the ceph-client relation exists, then the ceph-client relation
           must be ready as per the parent class
         * If the ceph-client relation does not exist, and local storage has
           been provided, then this will claim it is ready

        If none of the above are valid, then this will return False causing
        the charm to go into a waiting state.

        :return: True if the storage is ready, False otherwise.
        """
        if self.charm.has_ceph_relation():
            logger.debug("ceph relation is connected, deferring to parent")
            return super().ready

        # Check to see if the storage is satisfied
        if self.charm.has_local_storage():
            logger.debug(f"Storage {self.juju_storage_name} is attached")
            return True

        logger.debug(
            "Ceph relation does not exist and no local storage is "
            "available."
        )
        return False

    def context(self) -> dict:
        """Context for the ceph relation.

        :return:
        """
        if self.charm.has_ceph_relation():
            return super().context()
        return {}


class GlanceOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    ceph_conf = "/etc/ceph/ceph.conf"

    _state = StoredState()
    _authed = False
    service_name = "glance-api"
    wsgi_admin_script = "/usr/bin/glance-wsgi-api"
    wsgi_public_script = "/usr/bin/glance-wsgi-api"

    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "glance",
            "glance-manage",
            "--config-dir",
            "/etc/glance",
            "db",
            "sync",
        ]
    ]

    # ceph is included in the mandatory list as the GlanceStorage
    # relation handler falls back to juju storage if ceph relation
    # is not connected.
    mandatory_relations = {
        "database",
        "identity-service",
        "ingress-public",
        "ceph",
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.framework.observe(
            self.on.describe_status_action, self._describe_status_action
        )

    def _describe_status_action(self, event: EventBase) -> None:
        event.set_results({"output": self.status_pool.summarise()})

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        if self.has_ceph_relation():
            logger.debug("Application has ceph relation")
            contexts.append(
                sunbeam_ctxts.CephConfigurationContext(self, "ceph_config")
            )
            contexts.append(
                sunbeam_ctxts.CinderCephConfigurationContext(
                    self, "cinder_ceph"
                )
            )
        return contexts

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                self.service_conf,
                "root",
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/apache2/sites-enabled/glance-forwarding.conf",
                "root",
                self.service_group,
                0o640,
            ),
        ]
        if self.has_ceph_relation():
            _cconfigs.extend(
                [
                    sunbeam_core.ContainerConfigFile(
                        self.ceph_conf,
                        "root",
                        self.service_group,
                        0o640,
                    ),
                ]
            )
        return _cconfigs

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.ceph = GlanceStorageRelationHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name="rbd",
            juju_storage_name="local-repository",
        )
        handlers.append(self.ceph)
        return handlers

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/glance/glance-api.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "glance"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "glance"

    @property
    def service_endpoints(self):
        """Describe the glance service endpoint."""
        return [
            {
                "service_name": "glance",
                "type": "image",
                "description": "OpenStack Image",
                "internal_url": f"{self.internal_url}",
                "public_url": f"{self.public_url}",
                "admin_url": f"{self.admin_url}",
            }
        ]

    @property
    def default_public_ingress_port(self) -> int:
        """Default ingress port."""
        return 9292

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        # / returns 300 and /versions return 200
        return f"http://localhost:{self.default_public_ingress_port}/versions"

    def has_local_storage(self) -> bool:
        """Whether the application has been deployed with local storage or not.

        :return: True if local storage is present, False otherwise
        """
        storages = self.model.storages["local-repository"]
        return len(storages) > 0

    def has_ceph_relation(self) -> bool:
        """Returns whether or not the application has been related to Ceph.

        :return: True if the ceph relation has been made, False otherwise.
        """
        return self.model.get_relation("ceph") is not None

    def configure_charm(self, event) -> None:
        """Catchall handler to configure charm services."""
        not_ready_relations = self.get_mandatory_relations_not_ready(event)
        if not_ready_relations:
            logger.debug("Deferring configuration, charm relations not ready")
            return

        if self.has_ceph_relation():
            if not self.ceph.key:
                logger.debug("Ceph key is not yet present, waiting.")
                self.status.set(WaitingStatus("ceph key not present yet"))
                return
        elif self.has_local_storage():
            logger.debug("Local storage is configured, using that.")
        else:
            logger.debug("Neither local storage nor ceph relation exists.")
            self.status.set(
                BlockedStatus(
                    "Missing storage. Relate to Ceph "
                    "or add local storage to continue."
                )
            )
            return

        ph = self.get_named_pebble_handler("glance-api")
        if ph.pebble_ready:
            ph.execute(["a2enmod", "proxy_http"], exception_on_error=True)
            if self.has_ceph_relation() and self.ceph.key:
                # The code for managing ceph client config should move to
                # a shared lib as it is common across clients
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
            else:
                logger.debug("Using local storage")
            ph.init_service(self.contexts())

        super().configure_charm(event)
        if self.bootstrapped():
            for handler in self.pebble_handlers:
                handler.start_service()
            self.status.set(ActiveStatus(""))

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            GlanceAPIPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    def _on_config_changed(self, event: EventBase) -> None:
        self.configure_charm(event)
        if self.has_ceph_relation() and self.ceph.ready:
            logger.info("CONFIG changed and ceph ready: calling request pools")
            self.ceph.request_pools(event)


if __name__ == "__main__":
    main(GlanceOperatorCharm)
