# ironic-conductor-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

ironic-conductor-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharmK8S from the library.

ironic-conductor-k8s charm consumes database relation to connect to database,
amqp to connect to rabbitmq, and identity-credentials to connect to keystone.

The charm starts the ironic-conductor service.

## Intended use case

ironic-conductor-k8s charm deploys and configures OpenStack Ironic Conductor -
a bare metal provisioning service - on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- ironic-conductor-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- ironic-conductor-k8s

To deploy the local test instance:

    juju deploy ./ironic-conductor-k8s.charm ironic-conductor --trust \
      --resource ironic-conductor-image=ghcr.io/canonical/ironic-conductor:2024.1

To upgrade / refresh the ironic-conductor-k8s charm with a locally-built charm,
use the following command:

    juju refresh ironic-conductor --path ./ironic-conductor-k8s.charm


<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
