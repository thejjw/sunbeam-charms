# cinder-volume-hpe3par

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

cinder-volume-hpe3par charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharm from the library.

cinder-volume-hpe3par charm consumes database relation to connect to database,
amqp to connect to rabbitmq and hpe3par relation to connect to external HPE 3Par appliance.

The charm starts cinder-volume service with integration with hpe3par as
storage backend.

## Intended use case

cinder-volume-hpe3par charm deploys and configures OpenStack Block storage service
with hpe3par as backend storage on a kubernetes based environment.

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

    juju deploy ./cinder-volume-hpe3par.charm

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
