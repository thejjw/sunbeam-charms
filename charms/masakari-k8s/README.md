# masakari-k8s

## Description

masakari-k8s is an operator to manage the Masakari services masakari-api and 
masakari-engine in a Kubernetes-based environment.

## Usage

### Deployment

masakari k8s is deployed using below command:

    juju deploy masakari-k8s masakari --trust

Now connect the masakari operator to existing database, keystone identity,
and rabbitmq operators:

    juju relate masakari-k8s:ingress-public traefik-k8s:ingress
    juju relate masakari-k8s:identity-service keystone-k8s:identity-service
    juju relate masakari-k8s:database mysql-k8s:database
    juju relate masakari-k8s:amqp rabbitmq-k8s:amqp

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions masakari`.

## Relations

masakari-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`ingress-public`: To expose service on public network
`amqp`: To connect to Rabbitmq

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/masakari-consolidated:2025.1-24.04_edge

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-masakari-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-masakari-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-masakari-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
