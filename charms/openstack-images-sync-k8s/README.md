# openstack-images-sync-k8s

## Description

openstack-images-sync-k8s is an operator to manage openstack images sync on a Kubernetes based environment.
Openstack Images Sync is a tool to sync images from a Simplestreams source to an Openstack cloud.

## Usage

### Deployment

openstack-images-sync-k8s is deployed using below command:

    juju deploy openstack-images-sync-k8s openstack-images-sync

Now connect the openstack images sync operator to existing ingress, 
and keystone identity operators:

    juju relate keystone:identity-service openstack-images-sync:identity-service
    juju relate traefik-public:ingress openstack-images-sync:ingress-public
    juju relate traefik-internal:ingress openstack-images-sync:ingress-internal

### Configuration

This section covers common and/or important configuration options. See file
`charmcraft.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions openstack-images-sync`. If the charm is not
deployed then see file `charmcraft.yaml`.

## Relations

openstack-images-sync-k8s requires the following relations:

`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/openstack-images-sync:2025.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ois-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/openstack-images-sync-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-ois-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
