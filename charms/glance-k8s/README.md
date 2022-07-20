# glance-k8s

## Description

The glance-k8s is an operator to manage the image service on a
kubernetes based environment.

## Usage

### Deployment

glance-k8s is deployed using below command:

    juju deploy glance-k8s glance --trust

Now connect the glance application to an existing database,
amqp and keystone identity.

    juju relate mysql:database glance:shared-db
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

`shared-db`: To connect to the database
`amqp`: To connect to rabbitmq
`identity-service`: To register endpoints in keystone
`ceph`: To connect to ceph (optional)
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network


## OCI Images

The charm by default uses `docker.io/kolla/ubuntu-binary-glance:xena` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-glance-k8s].

<!-- LINKS -->

[contributors-guide]: https://github.com/openstack-charmers/charm-glance-operator/blob/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-glance-k8s]: https://bugs.launchpad.net/charm-glance-k8s/+filebug
