# glance-k8s

## Description

glance-k8s is an operator to manage the Glance image service on a
Kubernetes based environment.

## Usage

### Deployment

glance-k8s is deployed using below command:

    juju deploy glance-k8s glance --trust

Now connect the glance operator to an existing database,
messaging and keystone identity operators:

    juju relate mysql:database glance:database
    juju relate rabbitmq:amqp glance:amqp
    juju relate keystone:identity-service glance:identity-service

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions glance`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

glance-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ceph`: To connect to Ceph for image storage (optional)
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`ceph-rgw-ready`: To receive the RGW service readiness signal from the `microceph` charm

## OCI Images

The charm by default uses `ghcr.io/canonical/glance-api:2025.1` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-glance-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-glance-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-glance-k8s]: https://bugs.launchpad.net/charm-glance-k8s/+filebug
