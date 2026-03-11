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

"""Configure Octavia Amphora provider for CI tests.

Steps performed by configure_amphora():
  1. Deploy Multus CNI thick plugin on k8s.
  2. Set cni.exclusive=false on the Cilium Helm chart so Multus can co-exist.
  3. Deploy openstack-port-cni-k8s charm and relate it to keystone.
  4. Create lb-mgmt OpenStack resources:
       - lb-mgmt-net network + lb-mgmt-subnet (192.170.0.0/24)
       - Amphora test image (downloaded from tarballs.opendev.org)
       - Amphora Nova flavor (1 vCPU, 1 GiB RAM, 2 GiB disk)
       - lb-mgmt-sec-grp and lb-health-mgr-sec-grp security groups in the
         'services' project with the required ingress rules.
  5. Create a Multus NetworkAttachmentDefinition (ovs-lbmgmt) in the k8s
     model namespace, backed by openstack-port-cni.
  6. Generate Octavia CA and controller certificates using openssl.
  7. Configure the octavia juju application with all Amphora settings and
     wait for the unit to reach active/idle.
"""

import glob
import json
import logging
import os
import subprocess
import time
import yaml
from pathlib import Path

import jubilant
import zaza.model
from lightkube.config.kubeconfig import KubeConfig
from lightkube.core.client import Client as KubeClient
from lightkube.generic_resource import create_namespaced_resource
from zaza.openstack.utilities import openstack as openstack_utils

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The Multus thick-plugin daemonset manifest URL.
MULTUS_MANIFEST_URL = (
    "https://raw.githubusercontent.com/k8snetworkplumbingwg/multus-cni"
    "/master/deployments/multus-daemonset-thick.yml"
)

# The openstack-port-cni-k8s charm channel to deploy.
PORT_CNI_CHANNEL = "2026.1/edge"

# NetworkAttachmentDefinition name — matches amphora-network-attachment config.
NAD_NAME = "ovs-lbmgmt"

# lb-mgmt network/subnet parameters.
LB_MGMT_NET_NAME = "lb-mgmt-net"
LB_MGMT_SUBNET_NAME = "lb-mgmt-subnet"
LB_MGMT_SUBNET_CIDR = "192.170.0.0/24"

# Amphora image parameters.
AMPHORA_IMAGE_NAME = "amphora-x64-haproxy"
AMPHORA_IMAGE_TAG = "octavia-amphora"
AMPHORA_IMAGE_URL = (
    "https://tarballs.opendev.org/openstack/octavia/test-images"
    "/test-only-amphora-x64-haproxy-ubuntu-noble.qcow2"
)

# Amphora Nova flavor parameters.
AMPHORA_FLAVOR_NAME = "amphora"
AMPHORA_FLAVOR_VCPUS = 1
AMPHORA_FLAVOR_RAM_MB = 1024
AMPHORA_FLAVOR_DISK_GB = 2

# Security group names and tags.
LB_MGMT_SEC_GRP_NAME = "lb-mgmt-sec-grp"
LB_HEALTH_MGR_SEC_GRP_NAME = "lb-health-mgr-sec-grp"

# OVS bridge used by openstack-port-cni and the NAD resource annotation.
OVS_BRIDGE = "br-int"

# MicroOVN database socket path — required by openstack-port-cni.
MICROOVN_SOCKET = "unix:/var/snap/microovn/common/run/switch/db.sock"

# Multus resource annotation value for the OVS CNI bridge.
OVS_CNI_RESOURCE = f"ovs-cni.network.kubevirt.io/{OVS_BRIDGE}"

# Timeout (seconds) waiting for octavia to reach active/idle after config.
OCTAVIA_ACTIVE_TIMEOUT = 1800

# How long octavia must remain continuously settled before we consider it
# fully done.  This prevents returning during the brief (~6 second)
# active/idle windows that occur between successive pebble-ready and
# peers-relation-changed hooks executed after the pod restarts.
_STABILITY_WINDOW_SECS = 30

# Workload status message set by octavia while the Multus amphora-management
# network interface has not yet attached to the pod.  Used as an exclusion
# condition when waiting for octavia to settle — the same string is used by
# the sunbeam-python loadbalancer feature for the same purpose.
OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE = (
    "(amphora-network) Amphora management network interface not detected"
)

# ---------------------------------------------------------------------------
# OpenStack client helpers (via zaza keystone session)
# ---------------------------------------------------------------------------


def _get_clients():
    """Return (keystone_client, neutron_client, nova_client, glance_client)."""
    session = openstack_utils.get_overcloud_keystone_session()
    return (
        openstack_utils.get_keystone_session_client(session),
        openstack_utils.get_neutron_session_client(session),
        openstack_utils.get_nova_session_client(session),
        openstack_utils.get_glance_session_client(session),
    )


def _services_project_id(keystone_client):
    """Return the id of the 'services' project."""
    return openstack_utils.get_project_id(
        keystone_client, "services", domain_name=None
    )


# ---------------------------------------------------------------------------
# Step 1: Deploy Multus CNI thick plugin
# ---------------------------------------------------------------------------


def _deploy_multus():
    """Apply the Multus thick-plugin DaemonSet manifest on k8s."""
    logging.info("Deploying Multus CNI thick plugin: %s", MULTUS_MANIFEST_URL)
    subprocess.run(
        ["sudo", "k8s", "kubectl", "apply", "-f", MULTUS_MANIFEST_URL],
        check=True,
    )


# ---------------------------------------------------------------------------
# Step 2: Disable cni.exclusive on Cilium so Multus can attach extra interfaces
# ---------------------------------------------------------------------------


def _disable_cni_exclusive():
    """Upgrade the Cilium Helm chart with cni.exclusive=false."""
    logging.info("Setting cni.exclusive=false on k8s Cilium chart")
    pattern = "/snap/k8s/current/k8s/manifests/charts/cilium-*.tgz"
    matches = glob.glob(pattern)
    if not matches:
        raise FileNotFoundError(
            f"No Cilium chart found matching {pattern!r}; "
            "is the k8s snap installed?"
        )
    cilium_chart = matches[0]
    logging.info("Using Cilium chart: %s", cilium_chart)
    subprocess.run(
        [
            "sudo",
            "k8s",
            "helm",
            "upgrade",
            "-n",
            "kube-system",
            "ck-network",
            cilium_chart,
            "--set",
            "cni.exclusive=false",
        ],
        check=True,
    )


# ---------------------------------------------------------------------------
# Step 3: Deploy openstack-port-cni-k8s and relate to keystone
# ---------------------------------------------------------------------------


def _deploy_openstack_port_cni(juju_k8s):
    """Deploy openstack-port-cni-k8s and wait for it to become active."""
    status = juju_k8s.status()
    if "openstack-port-cni-k8s" in status.apps:
        logging.info("openstack-port-cni-k8s already deployed, skipping")
        return

    logging.info("Deploying openstack-port-cni-k8s from %s", PORT_CNI_CHANNEL)
    juju_k8s.cli(
        "deploy", "openstack-port-cni-k8s",
        "--channel", PORT_CNI_CHANNEL,
        "--base", "ubuntu@24.04",
        "--trust",
    )
    try:
        juju_k8s.cli(
            "integrate",
            "openstack-port-cni-k8s:identity-credentials",
            "keystone:identity-credentials",
        )
    except jubilant.CLIError as e:
        if "already exists" not in e.stderr:
            raise

    logging.info("Waiting for openstack-port-cni-k8s to become active")
    juju_k8s.wait(
        lambda status: jubilant.all_active(status, "openstack-port-cni-k8s"),
        timeout=600,
        delay=10,
    )


# ---------------------------------------------------------------------------
# Step 4: Create lb-mgmt OpenStack resources
# ---------------------------------------------------------------------------


def _create_lb_mgmt_network(neutron_client):
    """Create lb-mgmt-net and lb-mgmt-subnet. Returns (network_id, subnet_id)."""
    existing_nets = neutron_client.list_networks(name=LB_MGMT_NET_NAME)
    if existing_nets["networks"]:
        network = existing_nets["networks"][0]
        logging.info(
            "Network %r already exists (id=%s)", LB_MGMT_NET_NAME, network["id"]
        )
    else:
        logging.info("Creating network %r", LB_MGMT_NET_NAME)
        network = neutron_client.create_network(
            {"network": {"name": LB_MGMT_NET_NAME}}
        )["network"]

    existing_subnets = neutron_client.list_subnets(
        network_id=network["id"], name=LB_MGMT_SUBNET_NAME
    )
    if existing_subnets["subnets"]:
        subnet = existing_subnets["subnets"][0]
        logging.info(
            "Subnet %r already exists (id=%s)", LB_MGMT_SUBNET_NAME, subnet["id"]
        )
    else:
        logging.info(
            "Creating subnet %r (%s)", LB_MGMT_SUBNET_NAME, LB_MGMT_SUBNET_CIDR
        )
        subnet = neutron_client.create_subnet(
            {
                "subnet": {
                    "name": LB_MGMT_SUBNET_NAME,
                    "network_id": network["id"],
                    "cidr": LB_MGMT_SUBNET_CIDR,
                    "ip_version": 4,
                }
            }
        )["subnet"]

    return network["id"], subnet["id"]


def _upload_amphora_image(glance_client):
    """Download and upload the Amphora test image. Returns image_id."""
    existing = openstack_utils.get_images_by_name(glance_client, AMPHORA_IMAGE_NAME)
    if existing:
        logging.info(
            "Amphora image %r already exists (id=%s)",
            AMPHORA_IMAGE_NAME,
            existing[0].id,
        )
        return existing[0].id

    logging.info("Downloading and uploading Amphora image from %s", AMPHORA_IMAGE_URL)
    image = openstack_utils.create_image(
        glance_client,
        AMPHORA_IMAGE_URL,
        AMPHORA_IMAGE_NAME,
        tags=[AMPHORA_IMAGE_TAG],
        convert_image_to_raw_if_ceph_used=False,
    )
    logging.info("Uploaded Amphora image (id=%s)", image.id)
    return image.id


def _create_amphora_flavor(nova_client):
    """Create the Amphora Nova flavor. Returns flavor_id."""
    existing = [
        f for f in nova_client.flavors.list() if f.name == AMPHORA_FLAVOR_NAME
    ]
    if existing:
        logging.info(
            "Flavor %r already exists (id=%s)", AMPHORA_FLAVOR_NAME, existing[0].id
        )
        return existing[0].id

    logging.info(
        "Creating flavor %r (%d vCPU, %d MB RAM, %d GB disk)",
        AMPHORA_FLAVOR_NAME,
        AMPHORA_FLAVOR_VCPUS,
        AMPHORA_FLAVOR_RAM_MB,
        AMPHORA_FLAVOR_DISK_GB,
    )
    flavor = nova_client.flavors.create(
        name=AMPHORA_FLAVOR_NAME,
        vcpus=AMPHORA_FLAVOR_VCPUS,
        ram=AMPHORA_FLAVOR_RAM_MB,
        disk=AMPHORA_FLAVOR_DISK_GB,
    )
    return flavor.id


def _create_neutron_security_group(neutron_client, name, project_id):
    """Create a named security group in project_id if it doesn't exist.

    Returns the security group id.
    """
    existing = neutron_client.list_security_groups(
        name=name, project_id=project_id
    )["security_groups"]
    if existing:
        sg_id = existing[0]["id"]
        logging.info("Security group %r already exists (id=%s)", name, sg_id)
        return sg_id

    logging.info("Creating security group %r", name)
    sg = neutron_client.create_security_group(
        {"security_group": {"name": name, "tenant_id": project_id}}
    )["security_group"]
    return sg["id"]


def _neutron_sg_rule(
    neutron_client,
    sg_id,
    protocol,
    port_min=None,
    port_max=None,
    direction="ingress",
    remote_ip_prefix=None,
    ethertype="IPv4",
):
    """Add a rule to a security group, ignoring duplicates."""
    rule = {
        "security_group_id": sg_id,
        "direction": direction,
        "ethertype": ethertype,
        "protocol": protocol,
    }
    if port_min is not None:
        rule["port_range_min"] = port_min
        rule["port_range_max"] = port_max
    if remote_ip_prefix is not None:
        rule["remote_ip_prefix"] = remote_ip_prefix
    try:
        neutron_client.create_security_group_rule({"security_group_rule": rule})
    except Exception as exc:
        if "already exists" in str(exc) or "Duplicate" in str(exc):
            logging.debug("Security group rule already exists, skipping")
        else:
            raise


def _create_security_groups(neutron_client, keystone_client):
    """Create lb-mgmt-sec-grp and lb-health-mgr-sec-grp in the 'services' project.

    Returns (lb_sg_id, health_sg_id).
    """
    svc_project_id = _services_project_id(keystone_client)

    # --- lb-mgmt-sec-grp ---
    lb_sg_id = _create_neutron_security_group(
        neutron_client, LB_MGMT_SEC_GRP_NAME, svc_project_id
    )
    # Ingress from lb-mgmt subnet
    _neutron_sg_rule(neutron_client, lb_sg_id, "tcp", 9443, 9443,
                     remote_ip_prefix=LB_MGMT_SUBNET_CIDR)           # agent REST API
    _neutron_sg_rule(neutron_client, lb_sg_id, "icmp",
                     remote_ip_prefix=LB_MGMT_SUBNET_CIDR)           # health pings
    _neutron_sg_rule(neutron_client, lb_sg_id, "tcp", 22, 22,
                     remote_ip_prefix=LB_MGMT_SUBNET_CIDR)           # SSH
    # Egress to lb-mgmt subnet (controller → amphora)
    _neutron_sg_rule(neutron_client, lb_sg_id, None,
                     direction="egress",
                     remote_ip_prefix=LB_MGMT_SUBNET_CIDR)           # all protocols IPv4
    # Egress DNS + NTP (IPv4 and IPv6)
    for port in (53, 123):
        _neutron_sg_rule(neutron_client, lb_sg_id, "udp", port, port,
                         direction="egress")                          # IPv4
        _neutron_sg_rule(neutron_client, lb_sg_id, "udp", port, port,
                         direction="egress", ethertype="IPv6")       # IPv6

    # --- lb-health-mgr-sec-grp ---
    hm_sg_id = _create_neutron_security_group(
        neutron_client, LB_HEALTH_MGR_SEC_GRP_NAME, svc_project_id
    )
    # Ingress HM heartbeats from lb-mgmt subnet
    _neutron_sg_rule(neutron_client, hm_sg_id, "udp", 5555, 5555,
                     remote_ip_prefix=LB_MGMT_SUBNET_CIDR)           # HM heartbeats
    # Egress: user-created security groups have no default egress rules;
    # add explicit egress-all so the health-manager can reach the amphora
    # instances and other network services (DNS, NTP, etc.).
    _neutron_sg_rule(neutron_client, hm_sg_id, None,
                     direction="egress")                              # all protocols IPv4
    _neutron_sg_rule(neutron_client, hm_sg_id, None,
                     direction="egress", ethertype="IPv6")            # all protocols IPv6

    return lb_sg_id, hm_sg_id




# ---------------------------------------------------------------------------
# Step 5: Create Multus NetworkAttachmentDefinition
# ---------------------------------------------------------------------------


def _create_nad(k8s_namespace, network_id, subnet_id, lb_sg_id, hm_sg_id):
    """Create the ovs-lbmgmt NetworkAttachmentDefinition in k8s.

    The NAD uses the openstack-port-cni plugin backed by ovs-cni delegation
    on br-int.  The spec.config JSON embeds the lb-mgmt network/subnet IDs
    and both security group IDs so the daemon can wire the port correctly.

    The resource annotation
    ``k8s.v1.cni.cncf.io/resourceName: ovs-cni.network.kubevirt.io/br-int``
    tells the OVS CNI marker which host bridge to expose as a device resource.
    """
    logging.info(
        "Creating NetworkAttachmentDefinition %r in namespace %r",
        NAD_NAME,
        k8s_namespace,
    )
    home = Path(os.environ["HOME"])
    kubeconfig_file = home / "kubeconfig"
    if not kubeconfig_file.exists():
        raise FileNotFoundError(
            f"kubeconfig not found at {kubeconfig_file}; "
            "cannot create NetworkAttachmentDefinition"
        )

    with kubeconfig_file.open() as f:
        kubeconfig = KubeConfig.from_dict(yaml.safe_load(f))

    kube = KubeClient(kubeconfig, k8s_namespace, trust_env=False)

    NetworkAttachmentDefinition = create_namespaced_resource(
        group="k8s.cni.cncf.io",
        version="v1",
        kind="NetworkAttachmentDefinition",
        plural="network-attachment-definitions",
    )

    cni_config = json.dumps(
        {
            "cniVersion": "0.4.0",
            "type": "openstack-port-cni",
            "bridge": OVS_BRIDGE,
            "socket_file": MICROOVN_SOCKET,
            "subnet_id": subnet_id,
            "network_id": network_id,
            "delegate_plugin": "ovs",
            "security_group_ids": f"{lb_sg_id},{hm_sg_id}",
        },
        indent=2,
    )

    nad = NetworkAttachmentDefinition(
        metadata={
            "name": NAD_NAME,
            "namespace": k8s_namespace,
            "annotations": {
                "k8s.v1.cni.cncf.io/resourceName": OVS_CNI_RESOURCE,
            },
        },
        spec={"config": cni_config},
    )

    try:
        kube.create(nad)
        logging.info("Created NetworkAttachmentDefinition %r", NAD_NAME)
    except Exception as exc:
        if "already exists" in str(exc):
            logging.info(
                "NetworkAttachmentDefinition %r already exists, skipping", NAD_NAME
            )
        else:
            raise


# ---------------------------------------------------------------------------
# Step 6: Configure octavia juju application
# ---------------------------------------------------------------------------


def _configure_octavia(juju_k8s, network_id, flavor_id, lb_sg_id, hm_sg_id):
    """Set Amphora config options on the octavia application and wait for active.

    :param juju_k8s: jubilant.Juju handle for the k8s model
    :param network_id: id of the lb-mgmt-net Neutron network
    :param flavor_id: id of the Amphora Nova flavor
    :param lb_sg_id: id of the lb-health-mgr security group
    :param hm_sg_id: id of the health-manager security group

    Certificates are now provisioned via the amphora-issuing-ca and
    amphora-controller-cert tls-certificates relations (e.g. related to
    self-signed-certificates in CI).
    """
    config = {
        # Compute / image
        "amp-flavor-id": flavor_id,
        "amp-image-tag": AMPHORA_IMAGE_TAG,
        "amp-boot-network-list": network_id,
        "amp-secgroup-list": f"{lb_sg_id},{hm_sg_id}",
        # Multus network attachment
        "amphora-network-attachment": NAD_NAME,
    }

    logging.info("Applying Amphora config to octavia: %s", list(config))
    juju_k8s.config(app="octavia", values=config)

    # --- Phase 1: wait for config-changed to fire ---
    #
    # The agent goes non-idle as soon as Juju queues the config-changed hook.
    # If the hook has already fired and finished before we first poll, we
    # catch TimeoutError and move on (the agent is idle because it is done).
    logging.info("Waiting for octavia config-changed hook to fire")
    try:
        juju_k8s.wait(
            lambda status: not jubilant.all_agents_idle(status, "octavia"),
            timeout=120,
            delay=2,
        )
    except TimeoutError:
        logging.info(
            "octavia agent did not go non-idle within 120 s; "
            "config-changed may have already completed"
        )

    # --- Phase 2: wait for octavia to settle ---
    #
    # The config-changed hook patches the StatefulSet pod-template with the
    # Multus network annotation.  Kubernetes then performs a rolling update:
    # the octavia-0 pod is deleted and recreated with a new UID.  The charm
    # runs upgrade-charm → config-changed → pebble-ready → peers-relation-
    # changed hooks.  During this sequence:
    #
    #   * The agent alternates between "executing" and briefly "idle" (~6 s).
    #   * The workload is "blocked" with OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE
    #     while the Multus interface has not yet attached to the new pod.
    #   * Once the interface attaches, subsequent hooks transition the workload
    #     to "active" (certs are provided by self-signed-certificates in CI).
    #
    # We wait until all three conditions hold simultaneously for
    # _STABILITY_WINDOW_SECS seconds:
    #   1. Agent is idle (no hook running).
    #   2. Workload is active or blocked.
    #   3. Workload message is NOT OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE.
    #
    # This mirrors the sunbeam-python loadbalancer feature's
    # wait_until_desired_status call: both exclude the network-waiting blocked
    # message and accept the cert-waiting blocked state or active.
    logging.info(
        "Waiting for octavia to settle after Amphora config (stable for %d s)",
        _STABILITY_WINDOW_SECS,
    )
    _stable_since: list[float | None] = [None]

    def _is_settled(status) -> bool:
        app = status.apps.get("octavia")
        if not app:
            _stable_since[0] = None
            return False
        for unit in app.units.values():
            if unit.juju_status.current != "idle":
                _stable_since[0] = None
                return False
            if unit.workload_status.current not in ("active", "blocked"):
                _stable_since[0] = None
                return False
            if (unit.workload_status.message or "") == OCTAVIA_AMPHORA_NETWORK_WAITING_MESSAGE:
                _stable_since[0] = None
                return False
        # All units are in a stable terminal state.
        if _stable_since[0] is None:
            _stable_since[0] = time.monotonic()
        if time.monotonic() - _stable_since[0] >= _STABILITY_WINDOW_SECS:
            logging.info(
                "octavia has been stably settled for %d s", _STABILITY_WINDOW_SECS
            )
            return True
        return False

    juju_k8s.wait(
        _is_settled,
        timeout=OCTAVIA_ACTIVE_TIMEOUT,
        delay=10,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def configure_amphora():
    """Set up the Octavia Amphora provider for the microovn CI environment.

    This function is registered as a zaza configure step after
    ``deploy_machine_applications_microovn`` and after the basic OpenStack
    setup (endpoints, networks, keys) is complete.
    """
    k8s_model = zaza.model.get_juju_model()
    juju_k8s = jubilant.Juju(model=k8s_model)

    # --- 1. Multus ---
    _deploy_multus()

    # --- 2. Cilium cni.exclusive=false ---
    _disable_cni_exclusive()

    # --- 3. openstack-port-cni-k8s ---
    _deploy_openstack_port_cni(juju_k8s)

    # --- 4. OpenStack resources ---
    keystone_client, neutron_client, nova_client, glance_client = _get_clients()
    network_id, subnet_id = _create_lb_mgmt_network(neutron_client)
    _upload_amphora_image(glance_client)
    flavor_id = _create_amphora_flavor(nova_client)
    lb_sg_id, hm_sg_id = _create_security_groups(neutron_client, keystone_client)

    # --- 5. NetworkAttachmentDefinition ---
    _create_nad(k8s_model, network_id, subnet_id, lb_sg_id, hm_sg_id)

    # --- 6. Configure octavia + wait ---
    _configure_octavia(juju_k8s, network_id, flavor_id, lb_sg_id, hm_sg_id)
