# openstack-port-cni-k8s

A Juju charm that deploys the
[ovs-cni](https://github.com/k8snetworkplumbingwg/ovs-cni) upstream manifests
and the
[openstack-port-cni](https://github.com/canonical/openstack-port-cni)
DaemonSet on a Kubernetes cluster.

## Overview

### What gets deployed

| Component | Description |
|---|---|
| **ovs-cni** (upstream) | DaemonSet that installs the `ovs`, `ovs-mirror-producer`, and `ovs-mirror-consumer` CNI binaries on every node and runs the OVS marker, which exposes Open vSwitch bridges as Kubernetes node resources. |
| **openstack-port-cni** | DaemonSet with two containers: an initContainer that copies the thin `openstack-port-cni` binary to `/opt/cni/bin/`, and `openstack-port-daemon` which holds OpenStack credentials and manages Neutron ports via a Unix domain socket at `/var/run/openstack-cni/cni.sock`. |

### CNI call path

```
kubelet → openstack-port-cni → Unix socket → openstack-port-daemon → Neutron
                ↓
            ovs-cni (delegation)
```

## Relations

| Relation | Interface | Role | Notes |
|---|---|---|---|
| `identity-service` | `keystone` | requires | Provides OpenStack credentials (auth URL, username, password, project) injected into the daemon as a Kubernetes Secret. |

## Configuration

| Option | Default | Description |
|---|---|---|
| `image-registry` | `""` | Override the container registry prefix for all images.  Leave empty to use upstream registries. |
| `ovs-cni-release` | `v0.39.0` | Version of ovs-cni to deploy. |
| `openstack-port-cni-release` | `0.1.0` | Version of openstack-port-cni to deploy. |
| `region` | `RegionOne` | OpenStack region name. |

## Manifest management

Upstream manifest trees live under `upstream/` and are bundled into the charm
at build time.  New versions can be added by:

1. Creating a new subdirectory under `upstream/ovs-cni/manifests/<version>/`
   or `upstream/openstack-port-cni/manifests/<version>/` with the relevant
   YAML files.
2. Updating `upstream/ovs-cni/version` or `upstream/openstack-port-cni/version`
   to point at the new default.
3. Updating the `ovs-cni-release` / `openstack-port-cni-release` config
   defaults in `charmcraft.yaml`.

This pattern is inspired by
[charm-multus](https://github.com/charmed-kubernetes/charm-multus).

## Actions

| Action | Description |
|---|---|
| `list-versions` | List manifest versions bundled in this charm. |
| `list-resources` | List Kubernetes resources managed by this charm. |
| `scrub-resources` | Remove resources belonging to old manifest versions. |
| `sync-resources` | Re-apply any managed resources that are missing from the cluster. |

## Development

```bash
# Install dependencies
cd charms/openstack-port-cni-k8s
uv lock
uv sync

# Build the charm
charmcraft pack
```
