# ceilometer-k8s

## Description

ceilometer-k8s is an operator to manage the ceilometer services
ceilometer-central and ceilometer-notification on a Kubernetes
based environment.

## Usage

### Deployment

ceilometer-k8s is deployed using below command:

    juju deploy ceilometer-k8s ceilometer --trust

Now connect the ceilometer operator to keystone identity, rabbitmq
and gnocchi operators:

    juju relate keystone:identity-service ceilometer:identity-service
    juju relate rabbitmq:amqp ceilometer:amqp
    juju relate gnocchi:gnocchi-service ceilometer:gnocchi-db

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ceilometer`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

ceilometer-k8s requires the following relations:

`identity-service`: To register endpoints in Keystone
`amqp`: To connect to Rabbitmq
`gnocchi-db`: To connect to Gnocchi database

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/ceilometer-consolidated:2023.2

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ceilometer-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-ceilometer-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-ceilometer-k8s]: https://bugs.launchpad.net/charm-ceilometer-k8s/+filebug
