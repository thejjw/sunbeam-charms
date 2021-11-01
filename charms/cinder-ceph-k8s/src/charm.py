#!/usr/bin/env python3
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""Cinder Ceph Operator Charm.

This charm provide Cinder <-> Ceph integration as part
of an OpenStack deployment
"""

import logging

from ops.main import main
from ops.model import ActiveStatus

from typing import List

# NOTE: rename sometime
import advanced_sunbeam_openstack.core as core
import advanced_sunbeam_openstack.charm as charm
import advanced_sunbeam_openstack.relation_handlers as relation_handlers
import advanced_sunbeam_openstack.config_contexts as config_contexts
import advanced_sunbeam_openstack.container_handlers as container_handlers
import advanced_sunbeam_openstack.cprocess as cprocess

import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers

import charms.sunbeam_cinder_operator.v0.storage_backend as sunbeam_storage_backend

logger = logging.getLogger(__name__)


class CephConfigurationContext(config_contexts.ConfigContext):
    def context(self):
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


class CinderCephConfigurationContext(config_contexts.ConfigContext):
    def context(self):
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
            "secret_uuid": "f889b537-8d4e-4445-ae32-7552073e9b7e",
        }


class StorageBackendProvidesHandler(sunbeam_rhandlers.RelationHandler):
    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        sb_svc = sunbeam_storage_backend.StorageBackendProvides(
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
        return self.interface.remote_ready()


class CinderVolumePebbleHandler(container_handlers.PebbleHandler):
    def get_layer(self) -> dict:
        """cinder-volume service pebble layer

        :returns: pebble layer configuration for cinder-volume service
        """
        return {
            "summary": f"{self.service_name} layer",
            "description": "pebble config layer for cinder-volume service",
            "services": {
                self.service_name: {
                    "override": "replace",
                    "summary": self.service_name,
                    "command": f"{self.service_name} --use-syslog",
                    "startup": "enabled",
                },
            },
        }

    def start_service(self):
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

    def init_service(self, context):
        self.write_config(context)
        self.start_service()
        self._state.service_ready = True


class CinderCephOperatorCharm(charm.OSBaseOperatorCharm):
    """Cinder/Ceph Operator charm"""

    # NOTE: service_name == container_name
    service_name = "cinder-volume"

    service_user = "cinder"
    service_group = "cinder"

    cinder_conf = "/etc/cinder/cinder.conf"
    ceph_conf = "/etc/ceph/ceph.conf"

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(api_ready=False)

    def get_relation_handlers(self) -> List[relation_handlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.ceph = relation_handlers.CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name="rbd",
        )
        handlers.append(self.ceph)
        self.sb_svc = StorageBackendProvidesHandler(
            self, "storage-backend", self.api_ready
        )
        handlers.append(self.sb_svc)
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
                self.openstack_release,
                self.configure_charm,
            )
        ]

    def api_ready(self, event) -> None:
        self._state.api_ready = True
        self.configure_charm(event)
        if self._state.bootstrapped:
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
                    [self.service_name],
                    self.cinder_conf,
                    self.service_user,
                    self.service_group,
                ),
                core.ContainerConfigFile(
                    [self.service_name],
                    self.ceph_conf,
                    self.service_user,
                    self.service_group,
                ),
            ]
        )
        return _cconfigs

    @property
    def databases(self) -> List[str]:
        """Provide database name for cinder services"""
        return ["cinder"]

    def configure_charm(self, event) -> None:
        """Catchall handler to cconfigure charm services."""
        if not self.relation_handlers_ready():
            logging.debug("Defering configuration, charm relations not ready")
            return

        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                container = self.unit.get_container(ph.container_name)
                cprocess.check_call(
                    container,
                    [
                        "ceph-authtool",
                        f"/etc/ceph/ceph.client.{self.app.name}.keyring",
                        "--create-keyring",
                        f"--name=client.{self.app.name}",
                        f"--add-key={self.ceph.key}",
                    ],
                )
                ph.init_service(self.contexts())

        for ph in self.pebble_handlers:
            if not ph.service_ready:
                logging.debug("Defering, container service not ready")
                return

        self.unit.status = ActiveStatus()


class CinderCephWallabyOperatorCharm(CinderCephOperatorCharm):

    openstack_release = "wallaby"


if __name__ == "__main__":
    main(CinderCephWallabyOperatorCharm, use_juju_for_storage=True)
