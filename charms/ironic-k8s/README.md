# ironic-k8s

## Description

ironic-k8s is an operator to manage the Ironic API and noVNC proxy
services on a Kubernetes based environment.

## Usage

### Deployment

ironic-k8s is deployed using command below:

    juju deploy ironic-k8s ironic --trust

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the ironic operator to existing database, messaging,
keystone identity, and traefik operators:

    juju deploy mysql-router-k8s ironic-mysql-router --trust
    juju relate ironic-mysql-router:backend-database mysql:database
    juju relate ironic-mysql-router:database ironic:database
    juju relate rabbitmq:amqp ironic:amqp
    juju relate keystone:identity-service ironic:identity-service
    juju relate traefik:ingress ironic:ingress-internal
    juju relate traefik:traefik-route ironic:traefik-route-internal
    juju relate traefik-public:traefik-route ironic:traefik-route-public

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ironic`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

ironic-k8s requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `database`: To connect to MySQL.
- `identity-service`: To register endpoints in Keystone.
- `ingress-internal`: To expose service on underlying internal network.
- `traefik-route-internal`: To create an internal Traefik route for `ironic-novncproxy`.

The following relations are optional:

- `ingress-public`: To expose service on public network.
- `logging`: To send logs to Loki.
- `receive-ca-cert`: To enable TLS on the service endpoints.
- `tracing`: To connect to a tracing backend.
- `traefik-route-public`: To create a internal Traefik route for `ironic-novncproxy`.

## OCI Images

The charm by default uses following images:

- `ghcr.io/canonical/ironic-api:2025.1`
- `ghcr.io/canonical/ironic-novncproxy:2025.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ironic-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-ironic-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
