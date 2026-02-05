# manila-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

manila-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

manila-k8s charm consumes database relation to connect to database,
identity-service to register the service endpoints to keystone
and ingress-internal/ingress-public relation to get exposed over
internal and public networks.

## Intended use case

manila-k8s charm deploys and configures OpenStack Shared Filesystems
service on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- manila-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- manila-k8s

To deploy the local test instance:

    juju deploy ./manila-k8s.charm manila --trust \
      --resource manila-api-image=ghcr.io/canonical/manila-api:2024.1-24.04_edge \
      --resource manila-scheduler-image=ghcr.io/canonical/manila-scheduler:2024.1-24.04_edge

To upgrade / refresh the manila-k8s charm with a locally-built charm, use the
following command:

    juju refresh manila --path ./manila-k8s.charm


<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
