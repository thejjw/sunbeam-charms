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

import concurrent.futures
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
MACHINE_MICROOVN_BUNDLE_FILE = "./tests/openstack-microovn/bundles/machines.yaml"
K8S_WAIT_TIMEOUT = (
    3600  # 1 hour for k8s model wait (covers full pods provisioning)
)
POST_CMR_WAIT_TIMEOUT = 1800  # 30 minutes for post-CMR parallel wait


def replace_model_in_bundle(bundle: Path, words_to_replace: dict):
    """Replace words in a file."""
    content = bundle.read_text()
    for old_word, new_word in words_to_replace.items():
        logging.debug(f"Replacing {old_word} with {new_word}")
        content = content.replace(old_word, new_word)

    bundle.write_text(content)


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
    """Set up cross-model integrations for openstack and microovn tests.

    These integrations connect k8s-side charms (cinder, gnocchi,
    manila-cephfs) to their counterparts in the machines model.
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


def _wait_for_all_apps(juju, juju_k8s, machine_apps, k8s_apps):
    """Wait for machine apps and k8s apps to settle in parallel.

    Both models are polled concurrently so that their convergence
    time overlaps rather than being summed sequentially.

    A shared stop_event is passed to each wait so that if one side
    times out or errors, the other stops polling promptly rather than
    running for its full 1800s.
    """
    stop_event = threading.Event()

    def wait_machine():
        juju.wait(
            lambda status: stop_event.is_set() or jubilant.all_active(status, *machine_apps),
            timeout=1800,
        )

    def wait_k8s():
        juju_k8s.wait(
            lambda status: stop_event.is_set() or jubilant.all_active(status, *k8s_apps),
            timeout=1800,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(wait_machine), pool.submit(wait_k8s)]
        done, _ = futures_wait(futures, return_when=FIRST_EXCEPTION)
        # Signal the other thread to stop polling on first failure.
        stop_event.set()
        for f in done:
            f.result()  # re-raise any exception from the failed wait

def _all_non_active_apps_settled(status, non_active_apps):
    """Check non-active apps have reached their expected status.

    Apps in non_active_apps that do not exist in this model are silently
    skipped, so the same dict can be passed to both the machine and k8s
    model waits.

    :param status: jubilant Status object
    :param non_active_apps: dict mapping app name to expected workload
        status string
    """
    unready = []
    for app_name, expected_status in non_active_apps.items():
        app = status.apps.get(app_name)
        if app is None:
            continue  # app not in this model — skip
        for unit_name, unit in app.units.items():
            if (
                unit.workload_status.current != expected_status
                or unit.juju_status.current != "idle"
            ):
                unready.append(
                    f"{unit_name} "
                    f"(workload={unit.workload_status.current!r}, "
                    f"agent={unit.juju_status.current!r}, "
                    f"expected={expected_status!r}/idle)"
                )
    if unready:
        logging.info("Waiting for non-active apps: %s", ", ".join(unready))
        return False
    return True


def _wait_for_cmr_apps(
    juju, apps, active=False, timeout=POST_CMR_WAIT_TIMEOUT
):
    """Wait for k8s apps to have all units agent-idle (pods provisioned).

    If active=True, also requires workload_status == "active" (used after
    CMR is wired to confirm the apps have fully settled).

    :param juju: jubilant.Juju for the k8s model
    :param apps: list of app names to wait on
    :param active: if True, also require workload_status == "active"
    :param timeout: seconds before giving up
    """

    def _check(status):
        unready = []
        for app in apps:
            if app not in status.apps or not status.apps[app].units:
                continue
            for unit_name, unit in status.apps[app].units.items():
                if unit.juju_status.current != "idle" or (
                    active and unit.workload_status.current != "active"
                ):
                    unready.append(
                        f"{unit_name} "
                        f"(workload={unit.workload_status.current!r}, "
                        f"agent={unit.juju_status.current!r})"
                    )
        if unready:
            logging.info("Waiting for CMR apps: %s", ", ".join(unready))
            return False
        return True

    juju.wait(_check, delay=10, timeout=timeout)


def _wait_for_model(juju, non_active_apps, timeout=POST_CMR_WAIT_TIMEOUT):
    """Wait for all apps in a model to reach active/idle.

    Apps listed in non_active_apps are waited on with their specified
    workload status. All other apps in the model are expected to reach
    active/idle.
    """

    def _check(status):
        unready = [
            f"{unit_name} "
            f"(workload={unit.workload_status.current!r}, "
            f"agent={unit.juju_status.current!r})"
            for app_name, app in status.apps.items()
            if app_name not in non_active_apps and app.units
            for unit_name, unit in app.units.items()
            if unit.workload_status.current != "active"
            or unit.juju_status.current != "idle"
        ]
        if unready:
            logging.info("Waiting for model apps: %s", ", ".join(unready))
        return not unready and _all_non_active_apps_settled(
            status, non_active_apps
        )

    juju.wait(_check, delay=10, timeout=timeout)


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
        secret_uri = juju_k8s.add_secret(
            name=secret_name, content=secret_content
        )
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
        logging.error("Command failed with error: %s", e)
        raise e


def deploy_machine_applications():
    """Deploy machine apps and wait for all apps to reach target status.

    Deploys machine apps (hypervisor, microceph, cinder-volume, etc.).
    Order: wait for k8s CMR-offering apps to be provisioned, wire CMR,
    wait for machine apps to reach target status, wait for k8s CMR apps
    to reach active (cinder/gnocchi/manila-cephfs unblock once CMR is wired).
    """
    k8s_model = zaza.model.get_juju_model()
    target_deploy_status = lc_utils.get_charm_config().get(
        "target_deploy_status", {}
    )
    non_active_apps = {
        app: cfg["workload-status"]
        for app, cfg in target_deploy_status.items()
        if cfg.get("workload-status") != "active"
    }

    logging.debug("Updating machine bundle")
    bundle = MACHINE_BUNDLE_FILE
    replace_model_in_bundle(Path(bundle), {"K8S_MODEL": k8s_model})

    logging.info(bundle)
    juju_machine = jubilant.Juju(model=MACHINE_MODEL)
    juju_machine.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    juju_k8s = jubilant.Juju(model=k8s_model)

    _configure_magnum(juju_k8s)

    # 1. Wait for k8s apps to reach their target status.
    _wait_for_model(juju_k8s, non_active_apps, timeout=K8S_WAIT_TIMEOUT)

    # 2. Wire CMR — all k8s pods are provisioned and etcd load has subsided.
    _perform_common_cross_model_integrations(juju_machine, k8s_model)

    # 3 & 4. Wait for machine apps and k8s CMR apps in parallel now that
    # CMR is wired; machine model needs ceph credentials from microceph
    # while k8s CMR apps need the storage-backend/ceph integrations.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [
            ex.submit(
                _wait_for_model,
                juju_machine,
                non_active_apps,
                timeout=POST_CMR_WAIT_TIMEOUT,
            ),
            ex.submit(
                _wait_for_cmr_apps,
                juju_k8s,
                ["cinder", "gnocchi", "manila-cephfs"],
                active=True,
                timeout=POST_CMR_WAIT_TIMEOUT,
            ),
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()


def deploy_machine_applications_microovn():
    """Deploy Machine applications with MicroOVN as the OVN provider.

    Deploy machine applications like hypervisor, microceph, cinder-volume,
    microovn, sunbeam-ovn-proxy, and openstack-network-agents. In this
    scenario MicroOVN manages the full OVN stack (control plane + OVS on
    machines). There is no ovn-central-k8s or ovn-relay-k8s in the k8s model.

    sunbeam-ovn-proxy bridges microovn:ovsdb to the ovsdb-cms interface,
    serving the hypervisor in-bundle and providing a cross-model ovsdb-cms
    offer for neutron and octavia in the k8s model.

    Order: wait for k8s CMR-offering apps to be provisioned, wire CMR,
    wait for machine apps to reach target status, wait for k8s CMR apps
    to reach active (cinder/gnocchi/manila-cephfs/neutron/octavia unblock
    once CMR is wired).
    """
    k8s_model = zaza.model.get_juju_model()
    target_deploy_status = lc_utils.get_charm_config().get(
        "target_deploy_status", {}
    )
    non_active_apps = {
        app: cfg["workload-status"]
        for app, cfg in target_deploy_status.items()
        if cfg.get("workload-status") != "active"
    }

    logging.debug("Updating microovn machine bundle")
    bundle = MACHINE_MICROOVN_BUNDLE_FILE
    replace_model_in_bundle(Path(bundle), {"K8S_MODEL": k8s_model})

    logging.info(bundle)
    juju_machine = jubilant.Juju(model=MACHINE_MODEL)
    juju_machine.cli("deploy", str(bundle), "--map-machines=existing,0=0")

    juju_k8s = jubilant.Juju(model=k8s_model)

    _configure_magnum(juju_k8s)

    # 1. Wait for k8s apps to reach their target status.
    _wait_for_model(juju_k8s, non_active_apps, timeout=K8S_WAIT_TIMEOUT)

    # 2. Wire CMR — all k8s pods are provisioned and etcd load has subsided.
    _perform_common_cross_model_integrations(juju_machine, k8s_model)
    _perform_microovn_cross_model_integrations(juju_machine, k8s_model)

    # 3 & 4. Wait for machine apps and k8s CMR apps in parallel now that
    # CMR is wired; machine model needs ceph credentials from microceph
    # while k8s CMR apps need the storage-backend/ceph/ovsdb-cms integrations.
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        futures = [
            ex.submit(
                _wait_for_model,
                juju_machine,
                non_active_apps,
                timeout=POST_CMR_WAIT_TIMEOUT,
            ),
            ex.submit(
                _wait_for_cmr_apps,
                juju_k8s,
                ["cinder", "gnocchi", "manila-cephfs", "neutron", "octavia"],
                active=True,
                timeout=POST_CMR_WAIT_TIMEOUT,
            ),
        ]
        for future in concurrent.futures.as_completed(futures):
            future.result()

    _enable_microceph_orchestrator()
