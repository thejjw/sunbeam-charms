# glance-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

glance-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

glance-k8s charm consumes database relation to connect to database,
identity-service to register the service endpoints to keystone
and ingress-internal/ingress-public relation to get exposed over
internal and public networks.

## Intended use case

glance-k8s charm deploys and configures OpenStack Image service
on a kubernetes based environment. It should be possible to use
local storage or ceph based storage as backend.

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

    juju deploy ./glance-k8s_ubuntu-20.04-amd64.charm --trust --resource glance-image=ghcr.io/canonical/glance-api:2024.1

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/charm-ops-sunbeam/src/branch/main/README.rst
