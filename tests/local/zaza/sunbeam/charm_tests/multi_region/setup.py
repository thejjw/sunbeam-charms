# Copyright (c) 2025 Canonical Ltd.
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

import functools
import logging
import os
from pathlib import (
    Path,
)

import jubilant
import zaza.model
import zaza.sunbeam.charm_tests.machine.setup as machine_setup
import zaza.sunbeam.charm_tests.magnum.setup as magnum_setup
import zaza.sunbeam.charm_tests.vault_k8s.setup as vault_setup

PRIMARY_CONTROLLER = os.getenv("PRIMARY_CONTROLLER", "primary")
SECONDARY_CONTROLLER = os.getenv("SECONDARY_CONTROLLER", "manual")
SECONDARY_CLOUD = os.getenv("SECONDARY_CLOUD", "k8s")
SECONDARY_MODEL = "secondary-region"
SECONDARY_BUNDLE = "./tests/multi-region/bundles/secondary.yaml"
MACHINE_BUNDLE = "./tests/multi-region/bundles/machines.yaml"

SECONDARY_MODEL_WITH_CONTROLLER = SECONDARY_CONTROLLER + ":" + SECONDARY_MODEL

def deploy_secondary_region():
    """Deploy a secondary region."""

    logging.debug("Updating secondary bundle")
    bundle = SECONDARY_BUNDLE
    words_to_replace = {
        "PRIMARY_CONTROLLER": PRIMARY_CONTROLLER,
        "PRIMARY_MODEL": zaza.model.get_juju_model(),
    }
    machine_setup.replace_model_in_bundle(Path(bundle), words_to_replace)
    juju = jubilant.Juju()
    juju.add_model(
        SECONDARY_MODEL, cloud=SECONDARY_CLOUD, controller=SECONDARY_CONTROLLER
    )
    juju.model = SECONDARY_MODEL_WITH_CONTROLLER
    juju.deploy(SECONDARY_BUNDLE, trust=True)
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "glance",
            "ironic",
            "neutron",
            "nova",
            "nova-ironic",
            "octavia",
            "openstack-exporter",
            "openstack-images-sync",
            "ovn-central",
            "ovn-relay",
            "placement",
            "rabbitmq",
            "tls-operator",
        ),
        timeout=900,
    )



initialize_vault_secondary = functools.partial(
    vault_setup.initialize_vault_at_model,
    model=SECONDARY_MODEL_WITH_CONTROLLER,
)
configure_magnum_secondary = functools.partial(
    magnum_setup.configure_at_model,
    model=SECONDARY_MODEL_WITH_CONTROLLER,
)

def _wait_active_idle(status: jubilant.Status, *apps: str):
    return (
            jubilant.all_active(status, *apps)
            and jubilant.all_agents_idle(status, *apps)
        )

def deploy_machine_applications():
    """Deploy Machine applications.

    Deploy machine applications like hypervisor, microceph, cinder-volume.
    Perform necessary cross model integrations.
    Wait for the applications to be active.
    """
    k8s_model = zaza.model.get_juju_model()

    logging.debug("Updating machine bundle")
    bundle = MACHINE_BUNDLE
    words_to_replace = {
        "PRIMARY_MODEL": k8s_model,
        "PRIMARY_CONTROLLER": PRIMARY_CONTROLLER,
        "SECONDARY_CONTROLLER": SECONDARY_CONTROLLER,
        "SECONDARY_MODEL": SECONDARY_MODEL,
    }
    machine_setup.replace_model_in_bundle(Path(bundle), words_to_replace)

    logging.info(bundle)
    juju = jubilant.Juju(model=SECONDARY_CONTROLLER + ":" + machine_setup.MACHINE_MODEL)
    juju.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    # cinder-volume is in blocked as it needs to integrate with cinder-k8s
    try:
        juju.cli(
            "integrate",
            "--model",
            SECONDARY_MODEL_WITH_CONTROLLER,
            "cinder:storage-backend",
            f"{SECONDARY_CONTROLLER}:{machine_setup.MACHINE_MODEL_WITH_OWNER}.storage-backend",
            include_model=False,
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise e

    # Wait for machine applications to be in relevant status
    juju.wait(
        lambda status: _wait_active_idle(
            status,
            "microceph",
            "cinder-microceph",
            "hypervisor",
            "sunbeam-machine",
            "epa-orchestrator",
            "cinder-volume",
        ),
        timeout=1800,
    )

    juju_k8s = jubilant.Juju(model=SECONDARY_MODEL_WITH_CONTROLLER)
    juju_k8s.wait(
        lambda status: _wait_active_idle(
            status,
            "cinder",
        ),
        timeout=1200,
    )
