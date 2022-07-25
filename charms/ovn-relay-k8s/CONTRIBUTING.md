# ovn-relay-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

ovn-relay-k8s charm uses the ops\_sunbeam library and extends
OSBaseOVNOperatorCharm from the library.

ovn-relay-k8s charm consumes certificates to get generated
certificates from vault and ovsdb-cms relation to get
ovn-central endpoints.

## Intended use case

ovn-relay-k8s charm deploys and configures OVN OVSDB relay service
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

    juju deploy ./ovn-relay-k8s_ubuntu-20.04-amd64.charm --resource ovn-sb-db-server-image=registry.jujucharms.com/charm/kau2b145zhaeuj5ly4w4m30qiq8qzspf93tnd/ovn-sb-db-server-image

<!-- LINKS -->

[sunbeam-docs]: https://github.com/openstack-charmers/advanced-sunbeam-openstack/blob/main/README.rst
