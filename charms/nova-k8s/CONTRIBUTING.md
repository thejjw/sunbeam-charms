# nova-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

nova-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

nova-k8s charm consumes shared-db relation to connect to database,
identity-service to register the service endpoints to keystone
and ingress-internal/ingress-public relation to get exposed over
internal and public networks.

nova-k8s charm brings up nova-api, nova-scheduler, nova-conductor
services by creating separate peblle handlers for each service which
in turn creates separate container within same pod.

## Intended use case

nova-k8s charm deploys and configures OpenStack Nova service
on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

    ./run_tests

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox -e build

To deploy the local test instance:

    juju deploy ./nova-k8s_ubuntu-20.04-amd64.charm --trust --resource nova-api-image=kolla/ubuntu-binary-nova-api:xena --resource nova-scheduler-image=kolla/ubuntu-binary-nova-scheduler:xena nova-conductor-image=kolla/ubuntu-binary-nova-condcutor:xena

<!-- LINKS -->

[sunbeam-docs]: https://github.com/openstack-charmers/advanced-sunbeam-openstack/blob/main/README.rst
