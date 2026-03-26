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
import re
import subprocess
import threading
from concurrent.futures import (
    ThreadPoolExecutor,
    wait as futures_wait,
    FIRST_EXCEPTION,
)
from pathlib import (
    Path,
)

import jubilant
import zaza.charm_lifecycle.utils as lc_utils
import zaza.model

MACHINE_MODEL = "controller"
MACHINE_MODEL_WITH_OWNER = f"admin/{MACHINE_MODEL}"
MACHINE_BUNDLE_FILE = "./tests/openstack/bundles/machines.yaml"
WAIT_TIMEOUT = 3600  # 1 hour


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


def _all_non_active_apps_settled(status, non_active_apps):
    """Check that non-active apps present in the model have reached their expected status.

    Apps in non_active_apps that do not exist in this model are silently skipped,
    so the same dict can be passed to both the machine and k8s model waits.

    :param status: jubilant Status object
    :param non_active_apps: dict mapping app name to expected workload status string
    """
    for app_name, expected_status in non_active_apps.items():
        app = status.apps.get(app_name)
        if app is None:
            continue  # app not in this model — skip
        for unit in app.units.values():
            if unit.workload_status != expected_status or unit.agent_status != "idle":
                return False
    return True


def _wait_for_model(juju, non_active_apps, stop_event, timeout=WAIT_TIMEOUT):
    """Wait for all apps in a model to reach active/idle, except those in non_active_apps.

    Apps listed in non_active_apps are waited on with their specified workload status.
    All other apps in the model are expected to reach active/idle.
    """
    juju.wait(
        lambda status: (
            stop_event.is_set()
            or (
                all(
                    all(
                        unit.workload_status == "active"
                        and unit.agent_status == "idle"
                        for unit in app.units.values()
                    )
                    for app_name, app in status.apps.items()
                    if app_name not in non_active_apps
                    and app.units  # skip subordinates with no units
                )
                and _all_non_active_apps_settled(status, non_active_apps)
            )
        ),
        timeout=timeout,
    )


def _wait_for_all_apps(juju, juju_k8s, non_active_apps):
    """Wait for all apps in both models to settle in parallel.

    All apps in each model are expected active/idle unless listed in non_active_apps.
    Apps in non_active_apps that don't exist in a given model are silently skipped,
    so the same dict can safely be passed to both the machine and k8s model waits.

    A shared stop_event ensures the other thread stops promptly on first failure.
    """
    stop_event = threading.Event()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(_wait_for_model, juju, non_active_apps, stop_event),
            pool.submit(_wait_for_model, juju_k8s, non_active_apps, stop_event),
        ]
        done, _ = futures_wait(futures, return_when=FIRST_EXCEPTION)
        stop_event.set()
        for f in done:
            f.result()  # re-raise any exception from the failed wait


def _configure_magnum(juju_k8s):
    """Set up the magnum kubeconfig juju secret and configure the application.

    Creates (or reuses) a juju secret named 'kubeconfig', grants it to magnum,
    and sets the kubeconfig config option so magnum can proceed to active.
    """
    application = "magnum"
    secret_name = "kubeconfig"
    secret_content = {"kubeconfig": "fake-kubeconfig"}
    secret_not_found_pattern = r'ERROR secret ".*" not found'
    secret_uri: jubilant.secrettypes.SecretURI

    create_secret = False
    try:
        kubeconfig_secret = juju_k8s.show_secret(identifier=secret_name)
        secret_uri = kubeconfig_secret.uri
        logging.debug(f"Juju secret {secret_name} found")
    except jubilant.CLIError as e:
        match = re.search(secret_not_found_pattern, e.stderr)
        if not match:
            raise
        create_secret = True

    if create_secret:
        logging.debug(f"Create juju secret {secret_name}")
        secret_uri = juju_k8s.add_secret(name=secret_name, content=secret_content)
        juju_k8s.grant_secret(secret_uri, application)

    logging.info(f"Setting {application} kubeconfig option")
    juju_k8s.config(app=application, values={"kubeconfig": secret_uri})


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
    Does not wait for applications to be active; call wait_for_all_applications
    after all bundles are deployed.
    """
    k8s_model = zaza.model.get_juju_model()

    logging.debug("Updating machine bundle")
    bundle = MACHINE_BUNDLE_FILE
    replace_model_in_bundle(Path(bundle), {"K8S_MODEL": k8s_model})

    logging.info(bundle)
    juju = jubilant.Juju(model=MACHINE_MODEL)
    juju.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    # Wire cross-model integrations immediately after deploy; offers exist as
    # soon as the bundle is deployed so there is no need to wait for machine
    # apps to be active first.
    _perform_common_cross_model_integrations(juju, k8s_model)
    _configure_magnum(jubilant.Juju(model=k8s_model))


def wait_for_all_applications():
    """Wait for all machine and k8s applications to reach target status.

    Reads target_deploy_status from tests.yaml. Apps listed there are waited
    on with their specified workload status; all other apps in both models
    are expected to reach active/idle.
    """
    k8s_model = zaza.model.get_juju_model()
    target_deploy_status = lc_utils.get_charm_config().get("target_deploy_status", {})
    non_active_apps = {
        app: cfg["workload-status"]
        for app, cfg in target_deploy_status.items()
        if cfg.get("workload-status") != "active"
    }
    _wait_for_all_apps(
        jubilant.Juju(model=MACHINE_MODEL),
        jubilant.Juju(model=k8s_model),
        non_active_apps,
    )
    _enable_microceph_orchestrator()
