# cinder-volume-ceph

## Description

The cinder-volume-ceph is an operator to manage the Cinder service
integration with Ceph storage backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-ceph is deployed using below command:

    juju deploy cinder-volume-ceph  --trust

Now connect the cinder-ceph application to cinder-volume and Ceph
services:

    juju relate cinder-volume:cinder-volume cinder-ceph:cinder-volume
    juju relate ceph-mon:ceph cinder-ceph:ceph

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinderceph`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cinder-volume-ceph requires the following relations:

`cinder-volume`: To connect to Cinder service
`ceph`: To connect to Ceph storage backend


## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder-volume-ceph].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-cinder-volume-ceph/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
