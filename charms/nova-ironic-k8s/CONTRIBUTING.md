# nova-ironic-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

nova-ironic-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorCharmK8S from the library.

nova-ironic-k8s charm consumes database relation to connect to database,
amqp to connect to rabbitmq, and identity-credentials to connect to keystone.

The charm starts the nova-compute service for Ironic.

## Intended use case

nova-ironic-k8s charm deploys and configures the nova-compute service for
OpenStack Ironic - a bare metal provisioning service - on a kubernetes based
environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- nova-ironic-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- nova-ironic-k8s

To deploy the local test instance:

    juju deploy ./nova-ironic-k8s.charm nova-ironic --trust \
      --resource nova-ironic-image=ghcr.io/canonical/nova-ironic:2024.1-24.04_edge

To upgrade / refresh the nova-ironic-k8s charm with a locally-built charm,
use the following command:

    juju refresh nova-ironic --path ./nova-ironic-k8s.charm


<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
