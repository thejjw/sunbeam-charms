# watcher-k8s

## Description

watcher-k8s is an operator to manage the watcher services watcher api,
watcher decision engine and watcher applier on a Kubernetes based environment.

## Usage

### Deployment

watcher-k8s is deployed using below command:

    juju deploy watcher-k8s watcher --trust

Now connect the watcher operator to existing database, keystone identity,
and rabbitmq operators:

    juju relate mysql:database watcher:database
    juju relate keystone:identity-service watcher:identity-service
    juju relate rabbitmq:amqp watcher:amqp

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions watcher`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

watcher-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`amqp`: To connect to Rabbitmq

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/watcher-consolidated:2024.1-24.04_edge

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-watcher-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-watcher-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-watcher-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
