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


def replace_model_in_bundle(bundle: Path, words_to_replace: dict):
    """Replace words in a file."""
    content = bundle.read_text()
    for old_word, new_word in words_to_replace.items():
        logging.debug(f"Replacing {old_word} with {new_word}")
        modified_content = content.replace(old_word, new_word)

    bundle.write_text(modified_content)


def deploy_machine_applications():
    """Deploy Machine applications.

    Deploy machine applications like hypervisor, microceph, cinder-volume.
    Perform necessary cross model integrations.
    Wait for the applications to be active.
    """
    k8s_model = zaza.model.get_juju_model()

    logging.debug("Updating machine bundle")
    bundle = MACHINE_BUNDLE_FILE
    words_to_replace = {"K8S_MODEL": k8s_model}
    replace_model_in_bundle(Path(bundle), words_to_replace)

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
        timeout=1200,
    )

    # cinder-volume is in blocked as it needs to integrate with cinder-k8s
    try:
        juju.cli(
            "integrate",
            "--model",
            k8s_model,
            "cinder:storage-backend",
            f"{MACHINE_MODEL_WITH_OWNER}.storage-backend",
            include_model=False,
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise e

    try:
        juju.cli(
            "integrate",
            "--model",
            k8s_model,
            "gnocchi:ceph",
            f"{MACHINE_MODEL_WITH_OWNER}.ceph",
            include_model=False,
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise e

    try:
        juju.cli(
            "integrate",
            "--model",
            k8s_model,
            "manila-cephfs:ceph-nfs",
            f"{MACHINE_MODEL_WITH_OWNER}.ceph-nfs",
            include_model=False,
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise e

    juju.wait(
        lambda status: jubilant.all_active(status, "cinder-volume"),
        timeout=600,
    )

    juju_k8s = jubilant.Juju(model=k8s_model)
    juju_k8s.wait(
        lambda status: jubilant.all_active(
            status,
            "cinder",
            "ceilometer",
            "gnocchi",
            "manila",
            "manila-cephfs",
        ),
        timeout=1200,
    )

    # Workaround to enable Orchestrator module until
    # https://github.com/canonical/microceph/pull/611
    # is merged and published in squid/stable.
    try:
        subprocess.run(
            ["sudo", "microceh.ceph", "mgr", "module", "enable", "microceph"],
            check=True,
        )
        subprocess.run(
            ["sudo", "microcpeh.ceph", "orch", "set", "backend", "microceph"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with error: {e}")
        raise e
