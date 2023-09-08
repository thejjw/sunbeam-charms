# designate-k8s

## Description

designate-k8s is an operator to manage the designate services on a Kubernetes based environment.

## Usage

### Deployment

designate-k8s is deployed using below command:

    juju deploy designate-k8s designate --trust

Now connect the designate operator to existing database,
messaging, keystone identity, and bind9 operators:

    juju relate mysql:database designate:database
    juju relate rabbitmq:amqp designate:amqp
    juju relate keystone:identity-service designate:identity-service
    juju relate bind9:dns-backend designate:dns-backend

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions designate`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

designate-k8s requires the following relations:

- `database`: To connect to MySQL
- `amqp`: To connect to RabbitMQ
- `identity-service`: To register endpoints in Keystone
- `ingress-internal`: To expose service on underlying internal network
- `ingress-public`: To expose service on public network
- `dns-backend`: To register DNS records

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/designate-consolidated:2023.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-designate-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-designate-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-designate-k8s]: https://bugs.launchpad.net/charm-designate-k8s/+filebug
