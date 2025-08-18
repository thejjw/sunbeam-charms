# manila-data

## Description

The manila-data is an operator to manage the OpenStack manila-data service
in a snap based deployment.

## Usage

### Deployment

manila-data is deployed using the command below:

    juju deploy manila-data

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the manila-data operator to existing database, messaging,
and keystone identity operators:

    juju relate mysql:database manila-data:database
    juju relate rabbitmq:amqp manila-data:amqp
    juju relate keystone:identity-credentials manila-data:identity-credentials

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions manila-data`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

manila-data requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `database`: To connect to MySQL.
- `identity-credentials`: To connect to Keystone.

The following relations are optional:

- `logging`: To send logs to Loki.
- `tracing`: To connect to a tracing backend.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-manila-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-data/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-manila-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
