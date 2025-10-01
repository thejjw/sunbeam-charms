# ironic-conductor-k8s

## Description

ironic-conductor-k8s is an operator to manage the Ironic Conductor service on a
Kubernetes based environment.

## Usage

### Deployment

ironic-conductor-k8s is deployed using command below:

    juju deploy ironic-conductor-k8s ironic --trust

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the ironic-conductor operator to existing database, messaging,
and keystone identity:

    juju relate ironic-mysql-router:database ironic-conductor:database
    juju relate rabbitmq:amqp ironic-conductor:amqp
    juju relate keystone:identity-credentials ironic-conductor:identity-credentials
    juju relate microceph-ceph-rgw ironic-conductor:ceph-rgw

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions ironic-conductor`. If the charm
is not deployed then see file `actions.yaml`.

## Relations

ironic-conductor-k8s requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `database`: To connect to MySQL.
- `identity-credentials`: To connect to Keystone.
- `ceph-rgw`: To receive the RGW service readiness signal from the `microceph` charm.

The following relations are optional:

- `logging`: To send logs to Loki.
- `receive-ca-cert`: To enable TLS on the service endpoints.
- `tracing`: To connect to a tracing backend.

## OCI Images

The charm by default uses following images:

- `ghcr.io/canonical/ironic-conductor:2025.1`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-ironic-conductor-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-conductor-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-ironic-conductor-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
