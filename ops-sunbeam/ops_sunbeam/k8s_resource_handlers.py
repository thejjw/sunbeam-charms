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

"""Handles management of kubernetes resources."""

import logging

import ops_sunbeam.tracing as sunbeam_tracing
from lightkube.core.client import (
    Client,
)
from lightkube.models.core_v1 import (
    ServicePort,
    ServiceSpec,
)
from lightkube.models.meta_v1 import (
    ObjectMeta,
)
from lightkube.resources.core_v1 import (
    Service,
)
from lightkube_extensions.batch import (  # type: ignore[import-not-found]
    KubernetesResourceManager,
    create_charm_default_labels,
)
from ops.framework import (
    BoundEvent,
    Object,
)
from ops_sunbeam.charm import (
    OSBaseOperatorCharmK8S,
)

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class KubernetesLoadBalancerHandler(Object):
    """Manage Kubernetes LB services.

    Creates a new Kubernetes service of type Loadbalancer
    with name as {app.name}-lb. Patch the service on
    events defined by the charm.
    Remove the kubernetes service on removal of application
    or the last unit.
    """

    def __init__(
        self,
        charm: OSBaseOperatorCharmK8S,
        service_ports: list[ServicePort],
        refresh_event: list[BoundEvent] | None = None,
    ):
        super().__init__(charm, "kubernetes-lb-handler")
        self.charm = charm
        self._service_ports = service_ports
        self._lb_label = f"{self.charm.app.name}-lb"

        self._lightkube_client = None
        self._lightkube_field_manager: str = self.charm.app.name
        self._lb_name: str = f"{self.charm.app.name}-lb"

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, list):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._reconcile_lb)

        # Remove service if the last unit is removed
        self.framework.observe(charm.on.remove, self._on_remove)

    @property
    def lightkube_client(self):
        """Returns a lightkube client configured for this charm."""
        if self._lightkube_client is None:
            self._lightkube_client = Client(
                namespace=self.charm.model.name,
                field_manager=self._lightkube_field_manager,
            )
        return self._lightkube_client

    def _get_lb_resource_manager(self):
        return KubernetesResourceManager(
            labels=create_charm_default_labels(
                self.charm.app.name,
                self.charm.model.name,
                scope=self._lb_label,
            ),
            resource_types={Service},
            lightkube_client=self.lightkube_client,
            logger=logger,
        )

    def _construct_lb(self) -> Service:
        return Service(
            metadata=ObjectMeta(
                name=f"{self._lb_name}",
                namespace=self.charm.model.name,
                labels={"app.kubernetes.io/name": self.charm.app.name},
            ),
            spec=ServiceSpec(
                ports=self._service_ports,
                selector={"app.kubernetes.io/name": self.charm.app.name},
                type="LoadBalancer",
            ),
        )

    def _reconcile_lb(self, _) -> None:
        """Reconcile the LoadBalancer's state."""
        if not self.charm.unit.is_leader():
            return

        klm = self._get_lb_resource_manager()
        resources_list = [self._construct_lb()]
        logger.info(
            f"Patching k8s loadbalancer service object {self._lb_name}"
        )
        klm.reconcile(resources_list)

    def _on_remove(self, _) -> None:
        if not self.charm.unit.is_leader():
            return

        # juju scale down on kubernetes charms removes non-leader units.
        # So removal of leader unit can be considered as application is
        # getting destroyed or all the units are removed. Remove the
        # service in this case.
        logger.info(
            f"Removing k8s loadbalancer service object {self._lb_name}"
        )
        klm = self._get_lb_resource_manager()
        klm.delete()
