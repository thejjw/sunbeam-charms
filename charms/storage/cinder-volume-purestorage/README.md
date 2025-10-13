# cinder-volume-purestorage

## Description

The cinder-volume-purestorage is an operator to manage the Cinder service
integration with Pure Storage FlashArray backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-purestorage is deployed using below command:

    juju deploy cinder-volume-purestorage

Now connect the cinder-volume-purestorage application to the cinder-volume
service:

    juju relate cinder-volume:cinder-volume cinder-volume-purestorage:cinder-volume

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinder-volume-purestorage`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cinder-volume-purestorage requires the following relations:

`cinder-volume`: To connect to Cinder service


## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-sunbeam-charms].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/CONTRIBUTING.md
[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
