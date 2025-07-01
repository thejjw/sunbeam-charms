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

import functools
import logging
import re
from typing import (
    Dict,
    Optional,
    cast,
)

import ops_sunbeam.tracing as sunbeam_tracing
from lightkube.core.client import (
    Client,
)
from lightkube.core.exceptions import (
    ApiError,
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
from lightkube_extensions.batch import (  # type: ignore[import-untyped, import-not-found]
    KubernetesResourceManager,
    create_charm_default_labels,
)
from ops.framework import (
    BoundEvent,
    Object,
)
from ops.model import (
    BlockedStatus,
)
from ops_sunbeam.charm import (
    OSBaseOperatorCharmK8S,
)

logger = logging.getLogger(__name__)

# Regex for Kubernetes annotation values:
# - Allows alphanumeric characters, dots (.), dashes (-), and underscores (_)
# - Matches the entire string
# - Does not allow empty strings
# - Example valid: "value1", "my-value", "value.name", "value_name"
# - Example invalid: "value@", "value#", "value space"
ANNOTATION_VALUE_PATTERN = re.compile(r"^[\w.\-_]+$")

# Based on
# https://github.com/kubernetes/apimachinery/blob/v0.31.3/pkg/util/validation/validation.go#L204
# Regex for DNS1123 subdomains:
# - Starts with a lowercase letter or number ([a-z0-9])
# - May contain dashes (-), but not consecutively, and must not start or end with them
# - Segments can be separated by dots (.)
# - Example valid: "example.com", "my-app.io", "sub.domain"
# - Example invalid: "-example.com", "example..com", "example-.com"
DNS1123_SUBDOMAIN_PATTERN = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)

# Based on
# https://github.com/kubernetes/apimachinery/blob/v0.31.3/pkg/util/validation/validation.go#L32
# Regex for Kubernetes qualified names:
# - Starts with an alphanumeric character ([A-Za-z0-9])
# - Can include dashes (-), underscores (_), dots (.), or alphanumeric characters in the middle
# - Ends with an alphanumeric character
# - Must not be empty
# - Example valid: "annotation", "my.annotation", "annotation-name"
# - Example invalid: ".annotation", "annotation.", "-annotation", "annotation@key"
QUALIFIED_NAME_PATTERN = re.compile(
    r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$"
)


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

    @property
    def _loadbalancer_annotations(self) -> Optional[Dict[str, str]]:
        """Parses and returns annotations to apply to the LoadBalancer service.

        The annotations are expected as a string in the configuration,
        formatted as: "key1=value1,key2=value2,key3=value3". This string is
        parsed into a dictionary where each key-value pair corresponds to an annotation.

        Returns:
            Optional[Dict[str, str]]:
            A dictionary of annotations if provided in the Juju config and valid, otherwise None.
        """
        lb_annotations = cast(
            Optional[str],
            self.charm.config.get("loadbalancer_annotations", None),
        )
        return parse_annotations(lb_annotations)

    @property
    def _annotations_valid(self) -> bool:
        """Check if the annotations are valid.

        :return: True if the annotations are valid, False otherwise.
        """
        if self._loadbalancer_annotations is None:
            logger.error("Annotations are invalid or could not be parsed.")
            return False
        return True

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
                annotations=self._loadbalancer_annotations,
            ),
            spec=ServiceSpec(
                ports=self._service_ports,
                selector={"app.kubernetes.io/name": self.charm.app.name},
                type="LoadBalancer",
            ),
        )

    def _reconcile_lb(self, _):
        """Reconcile the LoadBalancer's state."""
        if not self.charm.unit.is_leader():
            return

        klm = self._get_lb_resource_manager()
        resources_list = []
        if self._annotations_valid:
            resources_list.append(self._construct_lb())
            logger.info(
                f"Patching k8s loadbalancer service object {self._lb_name}"
            )
        else:
            self.charm.status.set(
                BlockedStatus(
                    "Invalid config value 'loadbalancer_annotations'"
                )
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

    @functools.cache
    def get_loadbalancer_ip(self) -> str | None:
        """Helper to get loadbalancer IP.

        Result is cached for the whole duration of a hook.
        """
        try:
            svc = self.lightkube_client.get(
                Service, name=self._lb_name, namespace=self.model.name
            )
        except ApiError as e:
            logger.error(f"Failed to fetch LoadBalancer {self._lb_name}: {e}")
            return None

        if not (status := getattr(svc, "status", None)):
            return None
        if not (load_balancer_status := getattr(status, "loadBalancer", None)):
            return None
        if not (
            ingress_addresses := getattr(load_balancer_status, "ingress", None)
        ):
            return None
        if not (ingress_address := ingress_addresses[0]):
            return None

        return ingress_address.ip


def validate_annotation_key(key: str) -> bool:
    """Validate the annotation key."""
    if len(key) > 253:
        logger.error(
            f"Invalid annotation key: '{key}'. Key length exceeds 253 characters."
        )
        return False

    if not is_qualified_name(key.lower()):
        logger.error(
            f"Invalid annotation key: '{key}'. Must follow Kubernetes annotation syntax."
        )
        return False

    if key.startswith(("kubernetes.io/", "k8s.io/")):
        logger.error(
            f"Invalid annotation: Key '{key}' uses a reserved prefix."
        )
        return False

    return True


def validate_annotation_value(value: str) -> bool:
    """Validate the annotation value."""
    if not ANNOTATION_VALUE_PATTERN.match(value):
        logger.error(
            f"Invalid annotation value: '{value}'. Must follow Kubernetes annotation syntax."
        )
        return False

    return True


def parse_annotations(annotations: Optional[str]) -> Optional[Dict[str, str]]:
    """Parse and validate annotations from a string.

    logic is based on Kubernetes annotation validation as described here:
    https://github.com/kubernetes/apimachinery/blob/v0.31.3/pkg/api/validation/objectmeta.go#L44
    """
    if not annotations:
        return {}

    annotations = annotations.strip().rstrip(
        ","
    )  # Trim spaces and trailing commas

    try:
        parsed_annotations = {
            key.strip(): value.strip()
            for key, value in (
                pair.split("=", 1) for pair in annotations.split(",") if pair
            )
        }
    except ValueError:
        logger.error(
            "Invalid format for 'loadbalancer_annotations'. "
            "Expected format: key1=value1,key2=value2."
        )
        return None

    # Validate each key-value pair
    for key, value in parsed_annotations.items():
        if not validate_annotation_key(key) or not validate_annotation_value(
            value
        ):
            return None

    return parsed_annotations


def is_qualified_name(value: str) -> bool:
    """Check if a value is a valid Kubernetes qualified name."""
    parts = value.split("/")
    if len(parts) > 2:
        return False  # Invalid if more than one '/'

    if len(parts) == 2:  # If prefixed
        prefix, name = parts
        if not prefix or not DNS1123_SUBDOMAIN_PATTERN.match(prefix):
            return False
    else:
        name = parts[0]  # No prefix

    if not name or len(name) > 63 or not QUALIFIED_NAME_PATTERN.match(name):
        return False

    return True
