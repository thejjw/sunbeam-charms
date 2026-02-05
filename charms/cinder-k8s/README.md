# cinder-k8s

## Description

cinder-k8s is an operator to manage the Cinder API and Scheduler
services on a Kubernetes based environment.

## Usage

### Deployment

cinder-k8s is deployed using below command:

    juju deploy cinder-k8s cinder --trust

Now connect the cinder operator to existing database, messaging
and keystone identity operators:

    juju relate mysql:database cinder:database
    juju relate rabbitmq:amqp cinder:amqp
    juju relate keystone:identity-service cinder:identity-service

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinder`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cinder-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`storage-backend`: To connect to backends which manage block storage

## OCI Images

The charm by default uses follwoing images:

`ghcr.io/canonical/cinder-api:2024.1-24.04_edge`
`ghcr.io/canonical/cinder-scheduler:2024.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-cinder-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-cinder-k8s]: https://bugs.launchpad.net/charm-cinder-k8s/+filebug
