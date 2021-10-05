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

from charms.sunbeam_rabbitmq_operator.v0.amqp import AMQPRequires
from charms.ceph.v0.ceph_client import CephClientRequires

from typing import List
from collections.abc import Callable

# NOTE: rename sometime
import advanced_sunbeam_openstack.core as core
import advanced_sunbeam_openstack.adapters as adapters

logger = logging.getLogger(__name__)


class CinderCephAdapters(adapters.OPSRelationAdapters):
    @property
    def interface_map(self):
        _map = super().interface_map
        _map.update({"rabbitmq": adapters.AMQPAdapter})
        return _map


class AMQPHandler(core.RelationHandler):
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
        username: str,
        vhost: int,
    ):
        self.username = username
        self.vhost = vhost
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> Object:
        """Configure event handlers for an AMQP relation."""
        logger.debug("Setting up AMQP event handler")
        amqp = AMQPRequires(
            self.charm, self.relation_name, self.username, self.vhost
        )
        self.framework.observe(amqp.on.ready, self._on_amqp_ready)
        return amqp

    def _on_amqp_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Handler ready for use."""
        try:
            return bool(self.interface.password)
        except AttributeError:
            return False


class CephClientHandler(core.RelationHandler):
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        callback_f: Callable,
        broker_callback_f: Callable,
    ):
        self.broker_callback_f = broker_callback_f
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
            ceph.on.broker_available, self._on_broker_available
        )
        return ceph

    def _on_pools_available(self, event) -> None:
        """Handles pools available event."""
        # Ready is only emitted when the interface considers
        # that the relation is complete
        self.callback_f(event)

    def _on_broker_available(self, event) -> None:
        """Handles broker available event."""
        # Propagate event to charm class to allow desired
        # pool requests etc... to be created
        self.broker_callback_f(event)

    @property
    def ready(self) -> bool:
        """Handler ready for use."""
        return self.interface.pools_available


class CinderCephOperatorCharm(core.OSBaseOperatorCharm):
    """Cinder/Ceph Operator charm"""

    # NOTE: service_name == container_name
    service_name = "cinder-volume"

    service_user = "cinder"
    service_group = "cinder"

    cinder_conf = "/etc/cinder/cinder.conf"

    def __init__(self, framework):
        super().__init__(framework, adapters=CinderCephAdapters(self))

    def get_relation_handlers(self) -> List[core.RelationHandler]:
        """Relation handlers for the service."""
        # TODO: add ceph once we've written a handler class
        self.amqp = AMQPHandler(
            self,
            "amqp",
            self.configure_charm,
            username="cinder",
            vhost="openstack",
        )
        self.ceph = CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            self.request_pools,
        )
        return [
            self.amqp,
            self.ceph,
        ]

    @property
    def container_configs(self) -> List[core.ContainerConfigFile]:
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                core.ContainerConfigFile(
                    [self.service_name],
                    self.cinder_conf,
                    self.service_user,
                    self.service_group,
                )
            ]
        )
        return _cconfigs

    def configure_charm(self, event) -> None:
        """Catchall handler to cconfigure charm services."""
        if not self.relation_handlers_ready():
            logging.debug("Defering configuration, charm relations not ready")
            return

        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                ph.init_service()

        for ph in self.pebble_handlers:
            if not ph.service_ready:
                logging.debug("Defering, container service not ready")
                return

        self.unit.status = ActiveStatus()

    def request_pools(self, event) -> None:
        """Request Ceph pool creation when interface broker is ready"""
        config = self.model.config.get
        data_pool_name = config("rbd-pool-name") or self.app.name
        metadata_pool_name = (
            config("ec-rbd-metadata-pool") or f"{self.app.name}-metadata"
        )
        weight = config("ceph-pool-weight")
        replicas = config("ceph-osd-replication-count")
        if config("pool-type") == "erasure-coded":
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
                config("ec-profile-name") or f"{self.app.name}-profile"
            )
            # Metadata sizing is approximately 1% of overall data weight
            # but is in effect driven by the number of rbd's rather than
            # their size - so it can be very lightweight.
            metadata_weight = weight * 0.01
            # Resize data pool weight to accomodate metadata weight
            weight = weight - metadata_weight
            # Create erasure profile
            self.ceph.interface.create_erasure_profile(
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
            self.ceph.interface.create_erasure_pool(
                name=data_pool_name,
                erasure_profile=profile_name,
                weight=weight,
                allow_ec_overwrites=True,
            )
            self.ceph.interface.create_replicated_pool(
                name=metadata_pool_name, weight=metadata_weight
            )

        else:
            self.ceph.interface.create_replicated_pool(
                name=data_pool_name, replicas=replicas, weight=weight
            )


class CinderCephVictoriaOperatorCharm(CinderCephOperatorCharm):

    openstack_release = "victoria"


if __name__ == "__main__":
    main(CinderCephVictoriaOperatorCharm, use_juju_for_storage=True)
