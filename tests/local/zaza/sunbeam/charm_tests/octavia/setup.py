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

import base64
import glob
import json
import logging
import os
import subprocess
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
PORT_CNI_CHANNEL = "2025.1/edge"

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

# Certificate passphrase used for issuing CA key encryption.
CERT_PASSPHRASE = "foobar"

# Directory where generated certs are written.
CERT_DIR = Path("/tmp/octavia-certs")

# OVS bridge used by openstack-port-cni and the NAD resource annotation.
OVS_BRIDGE = "br-int"

# MicroOVN database socket path — required by openstack-port-cni.
MICROOVN_SOCKET = "unix:/var/snap/microovn/common/run/switch/db.sock"

# Multus resource annotation value for the OVS CNI bridge.
OVS_CNI_RESOURCE = f"ovs-cni.network.kubevirt.io/{OVS_BRIDGE}"

# Timeout (seconds) waiting for octavia to reach active/idle after config.
OCTAVIA_ACTIVE_TIMEOUT = 900

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


def _neutron_sg_rule(neutron_client, sg_id, protocol, port_min=None, port_max=None):
    """Add an ingress rule to a security group, ignoring duplicates."""
    rule = {
        "security_group_id": sg_id,
        "direction": "ingress",
        "protocol": protocol,
    }
    if port_min is not None:
        rule["port_range_min"] = port_min
        rule["port_range_max"] = port_max
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
    _neutron_sg_rule(neutron_client, lb_sg_id, "tcp", 9443, 9443)   # agent REST API
    _neutron_sg_rule(neutron_client, lb_sg_id, "icmp")              # health pings
    _neutron_sg_rule(neutron_client, lb_sg_id, "tcp", 22, 22)       # SSH

    # --- lb-health-mgr-sec-grp ---
    hm_sg_id = _create_neutron_security_group(
        neutron_client, LB_HEALTH_MGR_SEC_GRP_NAME, svc_project_id
    )
    _neutron_sg_rule(neutron_client, hm_sg_id, "udp", 5555, 5555)   # HM heartbeats
    # Allow all ingress (no protocol restriction — mirrors the CLI command)
    _neutron_sg_rule(neutron_client, hm_sg_id, None)

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
# Step 6: Generate Octavia certificates
# ---------------------------------------------------------------------------


def _generate_certs():
    """Generate the issuing CA and controller CA + cert bundle.

    Uses the controller CA as the issuing CA (standard Octavia test setup).
    Certificates are written to CERT_DIR and re-used on subsequent runs.

    Returns a dict:
        controller_ca    – bytes of controller_ca.pem
        controller_ca_key – bytes of controller_ca_key.pem
        controller_cert_bundle – bytes of controller_cert_bundle.pem
        passphrase       – the CERT_PASSPHRASE string (plaintext)
    """
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    (CERT_DIR / "demoCA" / "newcerts").mkdir(parents=True, exist_ok=True)
    for fname in ("index.txt", "index.txt.attr"):
        (CERT_DIR / "demoCA" / fname).touch()

    issuing_ca_key = CERT_DIR / "issuing_ca_key.pem"
    issuing_ca = CERT_DIR / "issuing_ca.pem"
    controller_ca_key = CERT_DIR / "controller_ca_key.pem"
    controller_ca = CERT_DIR / "controller_ca.pem"
    controller_key = CERT_DIR / "controller_key.pem"
    controller_csr = CERT_DIR / "controller.csr"
    controller_cert = CERT_DIR / "controller_cert.pem"
    controller_cert_bundle = CERT_DIR / "controller_cert_bundle.pem"

    subj = "/C=US/ST=Somestate/O=Org/CN=www.example.com"
    ssl_conf = "/etc/ssl/openssl.cnf"

    if controller_cert_bundle.exists():
        logging.info("Octavia certs already present in %s, reusing", CERT_DIR)
    else:
        logging.info("Generating Octavia CA and controller certificates")

        # Issuing CA (generated but not used directly in Octavia config —
        # the controller CA doubles as the issuing CA in this test setup).
        subprocess.run(
            [
                "openssl", "genrsa",
                "-passout", f"pass:{CERT_PASSPHRASE}",
                "-des3",
                "-out", str(issuing_ca_key),
                "2048",
            ],
            check=True,
        )
        subprocess.run(
            [
                "openssl", "req", "-x509",
                "-passin", f"pass:{CERT_PASSPHRASE}",
                "-new", "-nodes",
                "-key", str(issuing_ca_key),
                "-config", ssl_conf,
                "-subj", subj,
                "-days", "365",
                "-out", str(issuing_ca),
            ],
            check=True,
        )

        # Controller CA (used also as lb-mgmt-issuing-cacert in juju config).
        subprocess.run(
            [
                "openssl", "genrsa",
                "-passout", f"pass:{CERT_PASSPHRASE}",
                "-des3",
                "-out", str(controller_ca_key),
                "2048",
            ],
            check=True,
        )
        subprocess.run(
            [
                "openssl", "req", "-x509",
                "-passin", f"pass:{CERT_PASSPHRASE}",
                "-new", "-nodes",
                "-key", str(controller_ca_key),
                "-config", ssl_conf,
                "-subj", subj,
                "-days", "365",
                "-out", str(controller_ca),
            ],
            check=True,
        )

        # Controller cert + key (signed by the controller CA).
        subprocess.run(
            [
                "openssl", "req",
                "-newkey", "rsa:2048",
                "-nodes",
                "-keyout", str(controller_key),
                "-subj", subj,
                "-out", str(controller_csr),
            ],
            check=True,
        )
        subprocess.run(
            [
                "openssl", "ca",
                "-passin", f"pass:{CERT_PASSPHRASE}",
                "-config", ssl_conf,
                "-cert", str(controller_ca),
                "-keyfile", str(controller_ca_key),
                "-create_serial",
                "-batch",
                "-in", str(controller_csr),
                "-days", "365",
                "-out", str(controller_cert),
            ],
            cwd=str(CERT_DIR),
            check=True,
        )

        # Bundle: controller cert PEM + controller private key PEM.
        with controller_cert_bundle.open("wb") as bundle_fp:
            bundle_fp.write(controller_cert.read_bytes())
            bundle_fp.write(controller_key.read_bytes())

        logging.info("Certificates written to %s", CERT_DIR)

    return {
        "controller_ca": controller_ca.read_bytes(),
        "controller_ca_key": controller_ca_key.read_bytes(),
        "controller_cert_bundle": controller_cert_bundle.read_bytes(),
        "passphrase": CERT_PASSPHRASE,
    }


# ---------------------------------------------------------------------------
# Step 7: Configure octavia juju application
# ---------------------------------------------------------------------------


def _configure_octavia(juju_k8s, network_id, flavor_id, certs):
    """Set Amphora config options on the octavia application and wait for active.

    :param juju_k8s: jubilant.Juju handle for the k8s model
    :param network_id: id of the lb-mgmt-net Neutron network
    :param flavor_id: id of the Amphora Nova flavor
    :param certs: dict returned by _generate_certs()
    """
    def _b64(data: bytes) -> str:
        return base64.b64encode(data).decode()

    config = {
        # Compute / image
        "amp-flavor-id": flavor_id,
        "amp-image-tag": AMPHORA_IMAGE_TAG,
        "amp-boot-network-list": network_id,
        # Multus network attachment
        "amphora-network-attachment": NAD_NAME,
        # Certificates (base64-encoded PEM blobs)
        "lb-mgmt-issuing-cacert": _b64(certs["controller_ca"]),
        "lb-mgmt-issuing-ca-private-key": _b64(certs["controller_ca_key"]),
        "lb-mgmt-issuing-ca-key-passphrase": certs["passphrase"],
        "lb-mgmt-controller-cacert": _b64(certs["controller_ca"]),
        "lb-mgmt-controller-cert": _b64(certs["controller_cert_bundle"]),
    }

    logging.info("Applying Amphora config to octavia: %s", list(config))
    juju_k8s.config(app="octavia", values=config)

    logging.info("Waiting for octavia to become active after Amphora config")
    juju_k8s.wait(
        lambda status: jubilant.all_active(status, "octavia"),
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

    # --- 6. Certificates ---
    certs = _generate_certs()

    # --- 7. Configure octavia + wait ---
    _configure_octavia(juju_k8s, network_id, flavor_id, certs)
