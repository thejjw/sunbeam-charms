# openstack-exporter-k8s

## Description

openstack-exporter-k8s is an operator to manage an openstack exporter on a Kubernetes based environment.

## Usage

### Deployment

openstack-exporter-k8s is deployed using below command:

    juju deploy openstack-exporter-k8s openstack-exporter

Now connect the openstack exporter operator to existing keystone identity operators:

    juju relate keystone:identity-ops openstack-exporter:identity-ops

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions openstack-exporter`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

openstack-exporter-k8s requires the following relations:

`identity-ops`: To create admin user

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/openstack-exporter:1.6.0-7533071`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-openstack-exporter-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-openstack-exporter-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-openstack-exporter-k8s]: https://bugs.launchpad.net/charm-openstack-exporter-k8s/+filebug

