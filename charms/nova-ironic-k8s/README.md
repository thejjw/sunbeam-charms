# nova-ironic-k8s

## Description

nova-ironic-k8s is an operator to manage the nova-compute service for Ironic
on a Kubernetes based environment.

## Usage

### Deployment

nova-ironic-k8s is deployed using command below:

    juju deploy nova-ironic-k8s nova-ironic --trust

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the nova-ironic operator to existing database, messaging,
and keystone identity, ironic API, and traefik operators:

    juju relate nova-mysql-router:database nova-ironic:database
    juju relate rabbitmq:amqp nova-ironic:amqp
    juju relate keystone:identity-credentials nova-ironic:identity-credentials
    juju relate ironic:ironic-api nova-ironic:ironic-api
    juju relate traefik:traefik-route nova-ironic:traefik-route-internal
    juju relate traefik-public:traefik-route nova-ironic:traefik-route-public

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions nova-ironic`. If the charm
is not deployed then see file `actions.yaml`.

## Relations

nova-ironic-k8s requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `database`: To connect to MySQL.
- `identity-credentials`: To connect to Keystone.
- `ironic-api`: To receive the service readiness signal from the `ironic-k8s` charm.
- `traefik-route-internal`: To create an internal Traefik route for `nova-novncproxy`.

The following relations are optional:

- `logging`: To send logs to Loki.
- `receive-ca-cert`: To enable TLS on the service endpoints.
- `tracing`: To connect to a tracing backend.
- `traefik-route-public`: To create a internal Traefik route for `nova-novncproxy`.

## OCI Images

The charm by default uses following images:

- `ghcr.io/canonical/nova-ironic:2024.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-nova-ironic-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/nova-ironic-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-nova-ironic-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
