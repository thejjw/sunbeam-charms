# cloudkitty-k8s

## Description

cloudkitty-k8s is an operator to manage the cloudkitty API and Worker
services on a Kubernetes based environment.

## Usage

### Deployment

cloudkitty-k8s is deployed using below command:

    juju deploy cloudkitty-k8s cloudkitty --trust

Now connect the cloudkitty operator to existing database,
messaging and keystone identity operators:

    juju relate mysql:database cloudkitty:database
    juju relate rabbitmq:amqp cloudkitty:amqp
    juju relate keystone:identity-service cloudkitty:identity-service
    juju relate traefik:ingress cloudkitty:ingress-internal
    juju relate cloudkitty:metric-service gnocchi:metric-service
    # todo - gnocchi for fetcher and collector
    # todo - prometheus for fetcher and collector

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cloudkitty`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cloudkitty-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/cloudkitty-consolidated:2025.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cloudkitty-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-cloudkitty-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-cloudkitty-k8s]: https://bugs.launchpad.net/charm-cloudkitty-k8s/+filebug
