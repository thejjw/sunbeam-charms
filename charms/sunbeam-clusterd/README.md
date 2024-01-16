# sunbeam-clusterd

## Description

sunbeam-clusterd is an operator to manage the clusterd
service on a VM/Baremetal based environment.

## Usage

### Deployment

sunbeam-clusterd is deployed using below command:

    juju deploy sunbeam-clusterd


### Configuration

This section covers common and/or important configuration options. See file
`charmcraft.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions sunbeam-clusterd`. If the charm is not
deployed then see file `charmcraft.yaml`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-sunbeam-clusterd].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/sunbeam-clusterd/CONTRIBUTING.md
[juju-docs-actions]: https://juju.is/docs/juju/manage-actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-sunbeam-clusterd]: https://bugs.launchpad.net/sunbeam-charms/+filebug
