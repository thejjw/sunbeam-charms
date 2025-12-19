# manila-cephfs-k8s

## Description

manila-cephfs-k8s is an operator to manage the Manila Share for CephFS services
on a Kubernetes based environment. This operator will allow CEPHFS NFS Manila
shares to be created (`storage_protocol=NFS`).

## Usage

### Deployment

manila-cephfs-k8s is deployed using command below:

    juju deploy manila-cephfs-k8s manila-cephfs --trust

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the manila-cephfs operator to existing database, messaging, and
keystone identity, and manila operators:

    juju relate manila-mysql-router:database manila-cephfs:database
    juju relate rabbitmq:amqp manila-cephfs:amqp
    juju relate keystone:identity-credentials manila-cephfs:identity-credentials
    juju relate manila-cephfs:ceph-nfs admin/openstack-machines.microceph-ceph-nfs
    juju relate manila:manila manila-cephfs:manila

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions manila-cephfs`. If the charm is
not deployed then see file `actions.yaml`.

## Relations

manila-cephfs-k8s requires the following relations:

- `amqp`: To connect to RabbitMQ.
- `ceph-nfs`: To connect to the Ceph Cluster.
- `database`: To connect to MySQL.
- `identity-credentials`: To connect to Keystone.

The following relations are optional:

- `logging`: To send logs to Loki.
- `tracing`: To connect to a tracing backend.

The charm provides the following relation:

- `manila`: To provide Manila with the NFS storage backend.

## OCI Images

The charm by default uses follwoing images:

- `ghcr.io/canonical/manila-share:2025.1-24.04_edge`

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-manila-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-cephfs-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-manila-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
