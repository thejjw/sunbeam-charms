# ironic-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

ironic-k8s charm uses the ops\_sunbeam library and extends
OSBaseOperatorAPICharm from the library.

ironic-k8s charm consumes database relation to connect to database,
identity-service to register the service endpoints to keystone
and ingress-internal/ingress-public relation to get exposed over
internal and public networks.

The charms starts the ironic-api and ironic-novncproxy services.

## Intended use case

ironic-k8s charm deploys and configures OpenStack Ironic - a bare metal
provisioning service - on a kubernetes based environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- ironic-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- ironic-k8s

To deploy the local test instance:

    juju deploy ./ironic-k8s.charm ironic --trust \
      --resource ironic-api-image=ghcr.io/canonical/ironic-api:2024.1 \
      --resource ironic-novncproxy-image=ghcr.io/canonical/ironic-novncproxy:2024.1

To upgrade / refresh the ironic-k8s charm with a locally-built charm, use the
following command:

    juju refresh ironic --path ./ironic-k8s.charm


<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
