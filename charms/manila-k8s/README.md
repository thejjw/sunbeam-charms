# manila-k8s

## Description

manila-k8s is an operator to manage the Manila API and Scheduler
services on a Kubernetes based environment.

## Usage

### Deployment

manila-k8s is deployed using command below:

    juju deploy manila-k8s manila --trust

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the manila operator to existing database, messaging,
keystone identity, and traefik operators:

    juju deploy mysql-router-k8s manila-mysql-router --trust
    juju relate manila-mysql-router:backend-database mysql:database
    juju relate manila-mysql-router:database manila:database
    juju relate rabbitmq:amqp manila:amqp
    juju relate keystone:identity-service manila:identity-service
    juju relate traefik:ingress manila:ingress-internal

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions manila`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

manila-k8s requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `database`: To connect to MySQL.
- `identity-service`: To register endpoints in Keystone.
- `ingress-internal`: To expose service on underlying internal network.

The following relations are optional:

- `ingress-public`: To expose service on public network.
- `logging`: To send logs to Loki.
- `manila`: To connect Manila with a storage backend. At least one is required.
- `receive-ca-cert`: To enable TLS on the service endpoints.
- `tracing`: To connect to a tracing backend.

## OCI Images

The charm by default uses following images:

- `ghcr.io/canonical/manila-api:2025.1-24.04_edge`
- `ghcr.io/canonical/manila-scheduler:2025.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-manila-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-manila-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
