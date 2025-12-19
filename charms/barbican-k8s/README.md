# barbican-k8s

## Description

barbican-k8s is an operator to manage the Barbican API and Worker
services on a Kubernetes based environment.

## Usage

### Deployment

barbican-k8s is deployed using below command:

    juju deploy barbican-k8s barbican --trust

Now connect the barbican operator to existing database,
messaging and keystone identity operators:

    juju relate mysql:database barbican:database
    juju relate rabbitmq:amqp barbican:amqp
    juju relate keystone:identity-service barbican:identity-service
    juju relate vault:vault-kv barbican:vault-kv

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions barbican`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

barbican-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`identity-ops`: To register roles in Keystone (optional)
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`vault-kv`: To store secrets in a Vault key-value store

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/barbican-consolidated:2025.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-barbican-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-barbican-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-barbican-k8s]: https://bugs.launchpad.net/charm-barbican-k8s/+filebug
