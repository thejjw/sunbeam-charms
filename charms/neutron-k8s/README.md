# neutron-k8s

## Description

neutron-k8s is an operator to manage the Neutron networking service
on a Kubernetes based environment.

## Usage

### Deployment

neutron-k8s is deployed using below command:

    juju deploy neutron-k8s neutron --trust

Now connect the neutron operator to existing database,
messaging, identity, OVN and Vault operators:

    juju relate mysql:database neutron:database
    juju relate rabbitmq:amqp neutron:amqp
    juju relate keystone:identity-service neutron:identity-service
    juju relate ovn-central:ovsdb-cms neutron:ovsdb-cms
    juju relate vault:certificates neutron:certificates

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions neutron`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

neutron-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ovsdb-cms`: To connect to OVN
`certificates`: To retrieve generated certificates from Vault
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses `ghcr.io/canonical/neutron-server:2024.1` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-neutron-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-neutron-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-neutron-k8s]: https://bugs.launchpad.net/charm-neutron-k8s/+filebug
