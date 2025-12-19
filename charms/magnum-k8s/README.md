# magnum-k8s

## Description

magnum-k8s is an operator to manage the Magnul API and conductor services on a Kubernetes based environment.

## Usage

### Deployment

magnum-k8s is deployed using below command:

    juju deploy magnum-k8s magnum --trust

Now connect the magnum operator to existing database,
messaging and keystone identity operators:

    juju relate mysql:database magnum:database
    juju relate rabbitmq:amqp magnum:amqp
    juju relate keystone:identity-service magnum:identity-service
    juju relate keystone:identity-ops magnum:identity-ops

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions magnum`. If the charm is not
deployed then see file `actions.yaml`.

### Further information on testing

magnum-k8s support magnum-capi-helm driver and needs external kubernetes management
cluster.

Kubernetes management cluster should already have the Cluster API deployed.
Cluster API can be deployed by running following steps

    curl -L https://github.com/kubernetes-sigs/cluster-api/releases/download/v1.9.6/clusterctl-linux-amd64 -o clusterctl
    sudo install -o root -g root -m 0755 clusterctl /usr/local/bin/clusterctl
    KUBECONFIG=<kubeconfig file path> clusterctl init --core cluster-api:v1.9.6 --bootstrap canonical-kubernetes --control-plane canonical-kubernetes --infrastructure openstack:v0.11.3 --addon helm


Also Kubernetes cluster credentials should be passed as a juju secret to the
magnum charm via config option `kubeconfig`
Steps to create juju secret and update config

    juju add-secret secret-kubeconfig kubeconfig#file=<kubeconfig file path>
    juju grant-secret secret-kubeconfig magnum
    juju config magnum kubeconfig=<secret-kubeconfig URI>

## Relations

magnum-k8s requires the following relations:

`database`: To connect to MySQL
`amqp`: To connect to RabbitMQ
`identity-service`: To register endpoints in Keystone
`ingress-internal`: To expose service on underlying internal network
`ingress-public`: To expose service on public network
`identity-ops`: To create trustee user

## OCI Images

The charm by default uses following images:

    `ghcr.io/canonical/magnum-consolidated:2025.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-magnum-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-magnum-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-magnum-k8s]: https://bugs.launchpad.net/charm-magnum-k8s/+filebug
