#!/usr/bin/env python3
#
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

"""Deploy machine applications.

Deploy machine applications and integrations using jubilant.
"""

import logging
import subprocess
from pathlib import (
    Path,
)

import jubilant
import zaza.model

MACHINE_MODEL = "controller"
MACHINE_MODEL_WITH_OWNER = f"admin/{MACHINE_MODEL}"
MACHINE_BUNDLE_FILE = "./tests/openstack/bundles/machines.yaml"
MACHINE_MICROOVN_BUNDLE_FILE = "./tests/openstack-microovn/bundles/machines.yaml"


def replace_model_in_bundle(bundle: Path, words_to_replace: dict):
    """Replace words in a file."""
    content = bundle.read_text()
    for old_word, new_word in words_to_replace.items():
        logging.debug(f"Replacing {old_word} with {new_word}")
        modified_content = content.replace(old_word, new_word)

    bundle.write_text(modified_content)


def _integrate(juju, k8s_model, k8s_endpoint, machine_offer):
    """Create a cross-model integration, ignoring already-exists errors."""
    try:
        juju.cli(
            "integrate",
            "--model",
            k8s_model,
            k8s_endpoint,
            f"{MACHINE_MODEL_WITH_OWNER}.{machine_offer}",
            include_model=False,
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise e


def _perform_common_cross_model_integrations(juju, k8s_model):
    """Set up cross-model integrations required by both openstack and microovn tests.

    These integrations connect k8s-side charms (cinder, gnocchi, manila-cephfs)
    to their counterparts in the machines model.
    """
    # cinder-volume starts blocked until integrated with cinder-k8s
    _integrate(juju, k8s_model, "cinder:storage-backend", "storage-backend")
    _integrate(juju, k8s_model, "gnocchi:ceph", "ceph")
    _integrate(juju, k8s_model, "manila-cephfs:ceph-nfs", "ceph-nfs")


def _perform_microovn_cross_model_integrations(juju, k8s_model):
    """Set up cross-model integrations required by microovn tests.

    These integrations connect k8s-side charms (neutron, octavia)
    to their counterparts in the machines model.
    """
    _integrate(juju, k8s_model, "neutron:ovsdb-cms", "sunbeam-ovn-proxy")
    _integrate(juju, k8s_model, "octavia:ovsdb-cms", "sunbeam-ovn-proxy")


def _wait_after_integrations(juju, juju_k8s, extra_k8s_apps=None):
    """Wait for cinder-volume and k8s apps to settle after cross-model integrations."""
    juju.wait(
        lambda status: jubilant.all_active(status, "cinder-volume"),
        timeout=600,
    )

    k8s_apps = ["cinder", "ceilometer", "gnocchi", "manila", "manila-cephfs", "watcher"]
    if extra_k8s_apps:
        k8s_apps += extra_k8s_apps
    juju_k8s.wait(
        lambda status: jubilant.all_active(status, *k8s_apps),
        timeout=1200,
    )


def _enable_microceph_orchestrator():
    """Workaround to enable Orchestrator module.

    Required until https://github.com/canonical/microceph/pull/611
    is merged and published in squid/stable.
    """
    try:
        subprocess.run(
            ["sudo", "microceph.ceph", "mgr", "module", "enable", "microceph"],
            check=True,
        )
        subprocess.run(
            ["sudo", "microceph.ceph", "orch", "set", "backend", "microceph"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with error: {e}")
        raise e


def deploy_machine_applications():
    """Deploy Machine applications.

    Deploy machine applications like hypervisor, microceph, cinder-volume.
    Perform necessary cross model integrations.
    Wait for the applications to be active.
    """
    k8s_model = zaza.model.get_juju_model()

    logging.debug("Updating machine bundle")
    bundle = MACHINE_BUNDLE_FILE
    replace_model_in_bundle(Path(bundle), {"K8S_MODEL": k8s_model})

    logging.info(bundle)
    juju = jubilant.Juju(model=MACHINE_MODEL)
    juju.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    # Wait for machine applications to be in relevant status
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "microceph",
            "cinder-microceph",
            "hypervisor",
            "sunbeam-machine",
            "epa-orchestrator",
            "manila-data",
        ),
        timeout=1800,
    )

    _perform_common_cross_model_integrations(juju, k8s_model)
    _wait_after_integrations(juju, jubilant.Juju(model=k8s_model))
    _enable_microceph_orchestrator()


def deploy_machine_applications_microovn():
    """Deploy Machine applications with MicroOVN as the OVN provider.

    Deploy machine applications like hypervisor, microceph, cinder-volume,
    microovn, sunbeam-ovn-proxy, and openstack-network-agents. In this
    scenario MicroOVN manages the full OVN stack (control plane + OVS on
    machines). There is no ovn-central-k8s or ovn-relay-k8s in the k8s model.

    sunbeam-ovn-proxy bridges microovn:ovsdb to the ovsdb-cms interface,
    serving the hypervisor in-bundle and providing a cross-model ovsdb-cms
    offer for neutron and octavia in the k8s model.

    Perform necessary cross model integrations and wait for all applications
    to be active.
    """
    k8s_model = zaza.model.get_juju_model()

    logging.debug("Updating microovn machine bundle")
    bundle = MACHINE_MICROOVN_BUNDLE_FILE
    replace_model_in_bundle(Path(bundle), {"K8S_MODEL": k8s_model})

    logging.info(bundle)
    juju = jubilant.Juju(model=MACHINE_MODEL)
    juju.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    # Wait for all machine applications (including MicroOVN stack) to settle.
    # hypervisor gets ovsdb-cms locally from sunbeam-ovn-proxy (in bundle),
    # so it can reach active without any additional cross-model step.
    # openstack-network-agents is a subordinate on microovn units.
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "microceph",
            "cinder-microceph",
            "hypervisor",
            "sunbeam-machine",
            "epa-orchestrator",
            "manila-data",
            "microovn",
            "microcluster-token-distributor",
            "sunbeam-ovn-proxy",
            "openstack-network-agents",
        ),
        timeout=1800,
    )

    _perform_common_cross_model_integrations(juju, k8s_model)
    _perform_microovn_cross_model_integrations(juju, k8s_model)
    _wait_after_integrations(
        juju,
        jubilant.Juju(model=k8s_model),
        extra_k8s_apps=["neutron", "octavia"]
    )
    _enable_microceph_orchestrator()
