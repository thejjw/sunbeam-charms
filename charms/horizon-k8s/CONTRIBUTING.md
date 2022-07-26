# horizon-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarise with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

horizon-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

horizon-k8s charm consumes shared-db relation to connect to database,
identity-service to create cloud credentials and ingress-internal/
ingress-public relation to get exposed over internal and public networks.

## Intended use case

horizon-k8s charm deploys and configures OpenStack Dashboard service
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

    juju deploy ./horizon-k8s_ubuntu-20.04-amd64.charm --trust --resource openstack-dashboard-image=kolla/ubuntu-binary-horizon:xena

<!-- LINKS -->

[sunbeam-docs]: https://github.com/openstack-charmers/advanced-sunbeam-openstack/blob/main/README.rst
