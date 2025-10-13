# cinder-volume-ceph

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

cinder-volume-ceph charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharm from the library.

cinder-volume-ceph charm consumes database relation to connect to database,
amqp to connect to rabbitmq and ceph relation to connect to external ceph.

The charm starts cinder-volume service with integration with ceph as
storage backend.

## Intended use case

cinder-volume-ceph charm deploys and configures OpenStack Block storage service
with ceph as backend storage on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox -e py3

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox -e build

To deploy the local test instance:

    juju deploy ./cinder-volume-ceph.charm

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
