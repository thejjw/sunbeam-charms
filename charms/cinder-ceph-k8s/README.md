# cinder-ceph-k8s

## Description

The cinder-ceph-k8s is an operator to manage the Cinder service
integration with Ceph storage backend on a Kubernetes based
environment.

## Usage

### Deployment

cinder-ceph-k8s is deployed using below command:

    juju deploy cinder-ceph-k8s cinder-ceph --trust

Now connect the cinder-ceph application to database, messaging and Ceph
services:

    juju relate mysql:database cinder-ceph:database
    juju relate rabbitmq:amqp cinder-ceph:amqp
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

cinder-ceph-k8s requires the following relations:

`amqp`: To connect to RabbitMQ
`ceph`: To connect to Ceph storage backend
`database`: To connect to MySQL

## OCI Images

The charm by default uses `ghcr.io/canonical/cinder-volume:2025.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder-ceph-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-cinder-ceph-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-cinder-ceph-k8s]: https://bugs.launchpad.net/charm-cinder-ceph-k8s/+filebug
