# cinder-volume

## Description

The cinder-volume is an operator to manage the Cinder-volume service
in a snap based deployment.

## Usage

### Deployment

cinder-volume is deployed using below command:

    juju deploy cinder-volume

Now connect the cinder-volume application to database, messaging and Ceph
services:

    juju relate mysql:database cinder-volume:database
    juju relate rabbitmq:amqp cinder-volume:amqp
    juju relate keystone:identity-credentials cinder-volume:identity-credentials
    juju relate cinder:storage-backend cinder-volume:storage-backend

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinderceph`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cinder-volume requires the following relations:

`amqp`: To connect to RabbitMQ
`database`: To connect to MySQL
`identity-credentials`: To connect to Keystone

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

<!-- LINKS -->

[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
