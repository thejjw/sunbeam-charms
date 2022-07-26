# ovn-central-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

ovn-central-k8s charm uses the ops_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

ovn-central-k8s charm consumes certificates to get generated
certificates from vault and provides ovsdb-cms relation to
provide ovn-central endpoints for external services to connect to.

ovn-central-k8s starts northd, ovsdb-sb-server, ovsdb-nb-server
services by creating separate pebble handlers for each service.

## Intended use case

ovn-central-k8s charm deploys and configures OVN Central services
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

    juju deploy ./ovn-central-k8s_ubuntu-20.04-amd64.charm --trust --resource ovn-northd-image=registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-northd-image ovn-nb-db-server-image=registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-nb-db-server-image ovn-sb-db-server-image=registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-sb-db-server-image

<!-- LINKS -->

[sunbeam-docs]: https://github.com/openstack-charmers/advanced-sunbeam-openstack/blob/main/README.rst
