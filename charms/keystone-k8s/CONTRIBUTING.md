# keystone-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

keystone-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

The charm provides identity-service and identity-credentials relations
as a library to consume for other openstack charms and details are
documented [here](keystone-k8s-libs-docs). identity-service library
is consumed by charms that need to register to keystone catalog and
identity-credentials library is consumed by charms that want to create
cloud credentials.

keystone-k8s charm consumes database relation to connect to database
and ingress-internal/ingress-public relation to get exposed over
internal and public networks.

## Intended use case

keystone-k8s charm deploys and configures OpenStack Identity service
on a kubernetes based environment.

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

    juju deploy ./keystone-k8s_ubuntu-20.04-amd64.charm --trust --resource keystone-image=ghcr.io/canonical/keystone:2024.1-24.04_edge

<!-- LINKS -->

[keystone-k8s-libs-docs]: https://charmhub.io/sunbeam-keystone-operator/libraries/identity_service
[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
