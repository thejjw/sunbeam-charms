# aodh-k8s

## Description

aodh-k8s is an operator to manage the alarm services aodh api,
aodh evaluator, aodh notifier, aodh listener and aodh expirer
on a Kubernetes based environment.

## Usage

### Deployment

aodh-k8s is deployed using below command:

    juju deploy aodh-k8s aodh --trust

Now connect the aodh operator to existing database, keystone identity,
and rabbitmq operators:

    juju relate mysql:database aodh:database
    juju relate keystone:identity-service aodh:identity-service
    juju relate rabbitmq:amqp aodh:amqp

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions aodh`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

aodh-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`amqp`: To connect to Rabbitmq

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/aodh-consolidated:2024.1-24.04_edge

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-aodh-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-aodh-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-aodh-k8s]: https://bugs.launchpad.net/charm-aodh-k8s/+filebug
