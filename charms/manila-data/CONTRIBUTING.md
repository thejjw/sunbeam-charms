# manila-data

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

manila-data charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharmSnap from the library.

manila-data charm consumes database relation to connect to database,
amqp to connect to rabbitmq, and identity-credentials to connect to keystone.

The charm starts manila-data service.

## Intended use case

manila-data charm deploys and configures OpenStack manila-data service.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- manila-data

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- manila-data

To deploy the local test instance:

    juju deploy ./manila-data.charm manila-data

To upgrade / refresh the manila-k8s charm with a locally-built charm, use the
following command:

    juju refresh manila-data --path ./manila-data.charm

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
