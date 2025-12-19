# horizon-k8s

## Description

horizon-k8s is an operator to manage the OpenStack dashboard
service on a Kubernetes based environment.

## Usage

### Deployment

horizon-k8s is deployed using below command:

    juju deploy horizon-k8s horizon --trust

Now connect the horizon operator to existing database and Keystone
operators:

    juju relate mysql:database horizon:database
    juju relate keystone:identity-credentials horizon:identity-credentials

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions horizon`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

horizon-k8s requires the following relations:

`database`: To connect to MySQL
`identity-credentials`: To register cloud users in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network

## OCI Images

The charm by default uses `ghcr.io/canonical/horizon:2025.1-24.04_edge` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-horizon-k8s].


<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-horizon-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-horizon-k8s]: https://bugs.launchpad.net/charm-horizon-k8s/+filebug
