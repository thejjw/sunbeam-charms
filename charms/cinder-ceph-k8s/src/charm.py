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

from ops.charm import Object
from ops.main import main
from ops.model import ActiveStatus

from charms.sunbeam_rabbitmq_operator.v0.amqp import AMQPRequires

from typing import List

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
    def setup_event_handler(self) -> Object:
        """Configure event handlers for an AMQP relation."""
        logger.debug("Setting up AMQP event handler")
        amqp = AMQPRequires(
            self.charm, self.relation_name, "cinder", "openstack"
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
        self.amqp = AMQPHandler(self, "amqp", self.configure_charm)
        return [self.amqp]

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
            logging.debug(
                "Defering configuration, charm relations not ready"
            )
            return

        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                ph.init_service()

        for ph in self.pebble_handlers:
            if not ph.service_ready:
                logging.debug("Defering, container service not ready")
                return

        self.unit.status = ActiveStatus()


class CinderCephVictoriaOperatorCharm(CinderCephOperatorCharm):

    openstack_release = "victoria"


if __name__ == "__main__":
    main(CinderCephVictoriaOperatorCharm, use_juju_for_storage=True)
