# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
from pathlib import (
    Path,
)

import yaml
from lightkube.config.kubeconfig import (
    KubeConfig,
)
from lightkube.core.client import Client as KubeClient
from lightkube.core.exceptions import (
    ApiError,
    ConfigError,
)
from lightkube.resources.core_v1 import (
    Service,
)

METALLB_ANNOTATION = "metallb.universe.tf/loadBalancerIPs"


def add_loadbalancer_annotations(
    model: str = "openstack", lb_annotation: str = METALLB_ANNOTATION
):
    """Add loadbalancer annotations for all services of type LoadBalancer."""
    home: Path = Path(os.environ["HOME"])
    kubeconfig_file = home / "kubeconfig"

    if not kubeconfig_file.exists():
        logging.warning(
            "No kubeconfig file present, not adding k8s lb service annotations."
        )
        return

    try:
        with kubeconfig_file.open() as f:
            kubeconfig = KubeConfig.from_dict(yaml.safe_load(f))
    except (AttributeError, KeyError) as e:
        logging.warning("Error in kubeconfig content", exc_info=True)
        return

    try:
        kube = KubeClient(kubeconfig, model, trust_env=False)
        for service in kube.list(
            Service, namespace=model, fields={"spec.type": "LoadBalancer"}
        ):
            if not service.metadata:
                logging.warning(f"No metadata for service, {service}")
                continue

            service_name = str(service.metadata.name)
            service_annotations = service.metadata.annotations or {}
            if lb_annotation not in service_annotations:
                if not service.status:
                    logging.warning(
                        f"k8s service {service_name!r} has no status"
                    )
                    continue
                if not service.status.loadBalancer:
                    logging.warning(
                        f"k8s service {service_name!r} has no loadBalancer status"
                    )
                    continue
                if not service.status.loadBalancer.ingress:
                    logging.warning(
                        f"k8s service {service_name!r} has no loadBalancer ingress"
                    )
                    continue
                loadbalancer_ip = service.status.loadBalancer.ingress[0].ip
                service_annotations[lb_annotation] = loadbalancer_ip
                service.metadata.annotations = service_annotations
                logging.info(
                    f"Patching {service_name!r} to use IP {loadbalancer_ip!r}"
                )
                kube.patch(Service, service_name, obj=service)
    except ConfigError:
        logging.warning("Error creating k8s client", exc_info=True)
    except ApiError:
        logging.warning("Error getting services list", exc_info=True)
