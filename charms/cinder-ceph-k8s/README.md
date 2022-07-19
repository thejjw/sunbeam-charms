# cinder-ceph-k8s

## Description

The cinder-ceph-k8s is an operator to manage the cinder service 
integration with ceph storage backend on a kubernetes based
environment.

## Usage

### Deployment

cinder-ceph-k8s is deployed using below command:

    juju deploy cinder-ceph-k8s cinderceph --trust

Now connect the cinder-ceph application to database, amqp and ceph.

    juju relate mysql:database cinderceph:shared-db
    juju relate rabbitmq:amqp cinderceph:amqp
    juju relate ceph-mon:ceph cinderceph:ceph

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions cinderceph`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

cinder-ceph-k8s requires the following relations:

`amqp`: To connect to rabbitmq
`ceph`: To connect to ceph storage backend

## OCI Images

The charm by default uses `docker.io/kolla/ubuntu-binary-cinder-ceph:xena` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-cinder-ceph-k8s].

<!-- LINKS -->

[contributors-guide]: https://github.com/openstack-charmers/charm-cinder-ceph-operator/blob/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-cinder-ceph-k8s]: https://bugs.launchpad.net/charm-cinder-ceph-k8s/+filebug
