# neutron-baremetal-switch-config-k8s

## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

neutron-baremetal-switch-config-k8s charm uses the ops\_sunbeam library and
extends OSBaseOperatorCharmK8S from the library.

## Intended use case

The charm provides the necessary configuration for OpenStack Neutron's
`ml2_conf_networking_baremetal.ini` config file on a kubernetes-based
environment.

## Roadmap

TODO

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Run tests using command:

    tox --root ../../ -e py3 -- neutron-baremetal-switch-config-k8s

## Deployment

This project uses tox for building and managing. To build the charm
run:

    tox --root ../../ -e build -- neutron-baremetal-switch-config-k8s

To deploy the local test instance:

    juju deploy ./neutron-baremetal-switch-config-k8s.charm neutron-baremetal-config

To upgrade / refresh the nova-ironic-k8s charm with a locally-built charm,
use the following command:

    juju refresh neutron-baremetal-config --path ./neutron-baremetal-switch-config-k8s.charm


<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
