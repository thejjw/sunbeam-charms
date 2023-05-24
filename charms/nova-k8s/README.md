# nova-k8s

## Description

nova-k8s is an operator to manage the Nova API, Conductor and Scheduler
services on a Kubernetes based environment.

## Usage

### Deployment

nova-k8s is deployed using below command:

    juju deploy nova-k8s nova --trust

Now connect the nova operator to existing database,
messaging and keystone identity operators:

    juju relate mysql:database nova:database
    juju relate mysql:database nova:api-database
    juju relate mysql:database nova:cell-database
    juju relate rabbitmq:amqp nova:amqp
    juju relate keystone:identity-service nova:identity-service

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions nova`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

nova-k8s requires the following relations:

`database`, `api-database`, `cell-database`: To connect to MySQL (nova requires 3 databases)
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses following images:

    `ghcr.io/openstack-snaps/nova-api:2023.1`
    `ghcr.io/openstack-snaps/nova-scheduler:2023.1`
    `ghcr.io/openstack-snaps/nova-conductor:2023.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-nova-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-nova-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-nova-k8s]: https://bugs.launchpad.net/charm-nova-k8s/+filebug
