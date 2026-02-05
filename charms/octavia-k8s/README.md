# octavia-k8s

## Description

octavia-k8s is an operator to manage the octavia services octavia api,
octavia driver agent and octavia housekeeping on a Kubernetes based
environment. This charm supports only Octavia OVN provider.

## Usage

### Deployment

octavia-k8s is deployed using below command:

    juju deploy octavia-k8s octavia --trust

Now connect the octavia operator to existing database,
keystone identity, ovn-central and certificates operators:

    juju relate mysql:database octavia:database
    juju relate keystone:identity-service octavia:identity-service
    juju relate ovn-central:ovsdb-cms octavia:ovsdb-cms
    juju relate self-signed-certificates:certificates octavia:certificates

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions octavia`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

octavia-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`identity-ops`: To register roles in Keystone (optional)
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`ovsdb-cms`: To connect to OVN
`certificates`: To retreive generated certificates


## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/octavia-consolidated:2024.1-24.04_edge

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-octavia-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-octavia-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-octavia-k8s]: https://bugs.launchpad.net/charm-octavia-k8s/+filebug
