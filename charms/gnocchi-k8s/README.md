# gnocchi-k8s

## Description

gnocchi-k8s is an operator to manage the gnocchi services gnocchi api,
and gnocchi metricd on a Kubernetes based environment.

## Usage

### Deployment

gnocchi-k8s is deployed using below command:

    juju deploy gnocchi-k8s gnocchi --trust

Now connect the gnocchi operator to existing database, keystone identity,
and rabbitmq operators:

    juju relate mysql:database gnocchi:database
    juju relate keystone:identity-service gnocchi:identity-service

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions gnocchi`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

gnocchi-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/gnocchi-consolidated:2025.1

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-gnocchi-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-gnocchi-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-gnocchi-k8s]: https://bugs.launchpad.net/charm-gnocchi-k8s/+filebug
