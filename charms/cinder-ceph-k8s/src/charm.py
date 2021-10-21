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

from ops.charm import Object, CharmBase
from ops.main import main
from ops.model import ActiveStatus

from interface_ceph_client.ceph_client import CephClientRequires

from typing import List
from collections.abc import Callable

# NOTE: rename sometime
import advanced_sunbeam_openstack.core as core
import advanced_sunbeam_openstack.charm as charm
import advanced_sunbeam_openstack.relation_handlers as relation_handlers
import advanced_sunbeam_openstack.config_contexts as config_contexts
import advanced_sunbeam_openstack.container_handlers as container_handlers
import advanced_sunbeam_openstack.cprocess as cprocess

import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers

import charms.sunbeam_cinder_operator.v0.storage_backend \
    as sunbeam_storage_backend

logger = logging.getLogger(__name__)

ERASURE_CODED = "erasure-coded"
REPLICATED = "replacated"


class CephConfigurationContext(config_contexts.ConfigContext):
    def context(self):
        config = self.charm.model.config.get
        ctxt = {}
        if config("pool-type") and config("pool-type") == "erasure-coded":
            base_pool_name = config("rbd-pool") or config("rbd-pool-name")
            if not base_pool_name:
                base_pool_name = self.charm.app.name
            ctxt["rbd_default_data_pool"] = base_pool_name
        return ctxt


class CinderCephConfigurationContext(config_contexts.ConfigContext):
    def context(self):
        config = self.charm.model.config.get
        data_pool_name = config('rbd-pool-name') or self.charm.app.name
        if config('pool-type') == ERASURE_CODED:
            pool_name = (
                config('ec-rbd-metadata-pool') or
                f"{data_pool_name}-metadata"
            )
        else:
            pool_name = data_pool_name
        backend_name = config('volume-backend-name') or self.charm.app.name
        # TODO:
        # secret_uuid needs to be generated and shared for the app
        return {
            'cluster_name': self.charm.app.name,
            'rbd_pool': pool_name,
            'rbd_user': self.charm.app.name,
            'backend_name': backend_name,
            'backend_availability_zone': config('backend-availability-zone'),
            'secret_uuid': 'f889b537-8d4e-4445-ae32-7552073e9b7e',
        }


# TODO: -> aso
class CephClientHandler(relation_handlers.RelationHandler):
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
        allow_ec_overwrites: bool = True,
        app_name: str = None
    ):
        self.allow_ec_overwrites = allow_ec_overwrites
        self.app_name = app_name
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> Object:
        """Configure event handlers for an ceph-client interface."""
        logger.debug("Setting up ceph-client event handler")
        ceph = CephClientRequires(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ceph.on.pools_available, self._on_pools_available
        )
        self.framework.observe(
            ceph.on.broker_available, self.request_pools
        )
        return ceph

    def _on_pools_available(self, event) -> None:
        """Handles pools available event."""
        # Ready is only emitted when the interface considers
        # that the relation is complete
        self.callback_f(event)

    def request_pools(self, event) -> None:
        """
        Request Ceph pool creation when interface broker is ready.

        The default handler will automatically request erasure-coded
        or replicated pools depending on the configuration of the
        charm from which the handler is being used.

        To provide charm specific behaviour, subclass the default
        handler and use the required broker methods on the underlying
        interface object.
        """
        config = self.model.config.get
        data_pool_name = (
            config("rbd-pool-name") or
            config("rbd-pool") or
            self.charm.app.name
        )
        metadata_pool_name = (
            config("ec-rbd-metadata-pool") or f"{self.charm.app.name}-metadata"
        )
        weight = config("ceph-pool-weight")
        replicas = config("ceph-osd-replication-count")
        # TODO: add bluestore compression options
        if config("pool-type") == ERASURE_CODED:
            # General EC plugin config
            plugin = config("ec-profile-plugin")
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
            # Resize data pool weight to accomodate metadata weight
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
                name=data_pool_name, replicas=replicas, weight=weight,
                app_name=self.app_name,
            )

    @property
    def ready(self) -> bool:
        """Handler ready for use."""
        return self.interface.pools_available

    @property
    def key(self) -> str:
        """Retrieve the cephx key provided for the application"""
        return self.interface.get_relation_data().get('key')

    def context(self) -> dict:
        ctxt = super().context()
        data = self.interface.get_relation_data()
        ctxt['mon_hosts'] = ",".join(
            sorted(data.get("mon_hosts"))
        )
        ctxt['auth'] = data.get('auth')
        ctxt['key'] = data.get("key")
        ctxt['rbd_features'] = None
        return ctxt


class StorageBackendProvidesHandler(sunbeam_rhandlers.RelationHandler):

    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        sb_svc = sunbeam_storage_backend.StorageBackendProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            sb_svc.on.api_ready,
            self._on_ready)
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
            'summary': f'{self.service_name} layer',
            'description': 'pebble config layer for cinder-volume service',
            'services': {
                self.service_name: {
                    'override': 'replace',
                    'summary': self.service_name,
                    'command': f'{self.service_name} --use-syslog',
                    'startup': 'enabled',
                },
            },
        }

    def start_service(self):
        container = self.charm.unit.get_container(self.container_name)
        if not container:
            logger.debug(f'{self.container_name} container is not ready. '
                         'Cannot start service.')
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
        self.ceph = CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name='rbd'
        )
        handlers.append(self.ceph)
        self.sb_svc = StorageBackendProvidesHandler(
            self,
            'storage-backend',
            self.api_ready)
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
                self.configure_charm)]

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
        return ['cinder']

    def configure_charm(self, event) -> None:
        """Catchall handler to cconfigure charm services."""
        if not self.relation_handlers_ready():
            logging.debug("Defering configuration, charm relations not ready")
            return

        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                container = self.unit.get_container(
                    ph.container_name
                )
                cprocess.check_call(
                    container,
                    ['ceph-authtool',
                     f'/etc/ceph/ceph.client.{self.app.name}.keyring',
                     '--create-keyring',
                     f'--name=client.{self.app.name}',
                     f'--add-key={self.ceph.key}']
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
