# manila-cephfs-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

manila-cephfs-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharm from the library.

manila-cephfs-k8s charm consumes database relation to connect to database,
amqp to connect to rabbitmq.

The charm starts manila-share service.

## Intended use case

manila-cephfs-k8s charm deploys and configures OpenStack Manila Share
service for CephFS on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- manila-cephfs-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- manila-cephfs-k8s

To deploy the local test instance:

    juju deploy ./manila-cephfs-k8s.charm manila-cephfs --trust \
      --resource manila-share-image=ghcr.io/canonical/manila-share:2025.1-24.04_edge

To upgrade / refresh the manila-cephfs-k8s charm with a locally-built charm,
use the following command:

    juju refresh manila-cephfs --path ./manila-cephfs-k8s.charm

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
