# heat-k8s

## Description

heat-k8s is an operator to manage the orchestration services heat api,
heat api cfn and heat engine on a Kubernetes based environment.

## Usage

### Deployment

heat-k8s is deployed using below command:

    juju deploy heat-k8s heat --trust

Now connect the heat operator to existing database, keystone identity,
keystone ops and rabbitmq operators:

    juju relate mysql:database heat:database
    juju relate keystone:identity-service heat:identity-service
    juju relate keystone:identity-ops heat:identity-ops
    juju relate rabbitmq:amqp heat:amqp 

heat-api-cfn is deployed as separate instance of charm using below command:

    juju deploy heat-k8s heat-cfn --trust --config api_service=heat-api-cfn

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

#### `api_service`

The `api_service` option determines whether to act as heat-api service or
heat-api-cfn service. Accepted values are `heat-api` or `heat-api-service`
and defaults to `heat-api`.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions heat`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

heat-k8s requires the following relations:

`database`: To connect to MySQL
`identity-service`: To register endpoints in Keystone
`identity-ops`: To create heat stack domain and users
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`amqp`: To connect to Rabbitmq

## OCI Images

The charm by default uses following images:

    ghcr.io/canonical/heat-consolidated:2025.1-24.04_edge

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-heat-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-heat-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-heat-k8s]: https://bugs.launchpad.net/charm-heat-k8s/+filebug
