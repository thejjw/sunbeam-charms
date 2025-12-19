# placement-k8s

## Description

placement-k8s is an operator to manage the OpenStack Placement service
on a Kubernetes based environment.

## Usage

### Deployment

placement-k8s is deployed using below command:

    juju deploy placement-k8s placement --trust

Now connect the placement operator to an existing database
and keystone identity operators.

    juju relate mysql:database placement:database
    juju relate keystone:identity-service placement:identity-service

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions placement`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

placement-k8s requires the following relations:

`database`: To connect to the database
`identity-service`: To register endpoints in keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses `ghcr.io/canonical/placement-api:2025.1-24.04_edge` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-placement-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-placement-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-placement-k8s]: https://bugs.launchpad.net/charm-placement-k8s/+filebug
