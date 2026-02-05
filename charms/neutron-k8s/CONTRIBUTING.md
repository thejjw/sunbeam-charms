# neutron-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

neutron-k8s charm uses the ops_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

neutron-k8s charm consumes database relation to connect to database,
identity-service to register the service endpoints to keystone,
ovsdb-cms to connect to ovn-central, certificates to get generated
certificates from vault and ingress-internal/ingress-public relation
to get exposed over internal and public networks.

## Intended use case

neutron-k8s charm deploys and configures OpenStack Neutron service
on a kubernetes based environment. The charm supports configurations
to integrate neutron with OVN.

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

    juju deploy ./neutron-k8s_ubuntu-20.04-amd64.charm --trust --resource neutron-server-image=ghcr.io/canonical/neutron-server:2024.1-24.04_edge

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
