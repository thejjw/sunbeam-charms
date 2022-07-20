# ovn-central-k8s

## Description

The ovn-central-k8s is an operator to manage the OVN central
services northd, ovsdb-nb, ovsdb-sb on a kubernetes based
environment.

## Usage

### Deployment

ovn-central-k8s is deployed using below command:

    juju deploy ovn-central-k8s ovn-central

Now connect the ovn-central application to vault to generate
certificates.

    juju relate vault:certificates ovn-central:certificates

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ovn-central`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

ovn-central-k8s requires the following relations:
`certificates`: To retrieve generated certificates from vault

ovn-central-k8s provides the following relations:
`ovsdb-cms`: Used by neutron to get ovn-central IPs.

## OCI Images

The charm by default uses following images.
    `registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-northd-image`
    `registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-nb-db-server-image`
    `registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-sb-db-server-image`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ovn-central-k8s].

<!-- LINKS -->

[contributors-guide]: https://github.com/openstack-charmers/charm-ovn-central-operator/blob/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-ovn-central-k8s]: https://bugs.launchpad.net/charm-ovn-central-k8s/+filebug
