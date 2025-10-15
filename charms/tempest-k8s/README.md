<!--
Avoid using this README file for information that is maintained or published elsewhere, e.g.:

* metadata.yaml > published on Charmhub
* documentation > published on (or linked to from) Charmhub
* detailed contribution guide > documentation or CONTRIBUTING.md

Use links instead.
-->

# tempest-k8s

Tempest provides a set of integration tests to be run, in ad-hoc
or periodic fasion, against a live OpenStack cluster for OpenStack API
validation, scenarios, and other specific tests useful in validating an
OpenStack deployment.

## Usage

### Ironic Tempest tests

In order to run the Ironic Tempest tests, the following items are required:

- the Ironic charms (`ironic-k8s`, `nova-ironic-k8s`, `ironic-conductor-k8s`)
  must be deployed, and in an active state.
- the `ironic-k8s` and `neutron-k8s` charms must be related.
- the `glance-k8s` charm must have the `ceph-rgw` relation set.
- `ironic-conductor-k8s`'s charm `cleaning-network` config option must be set.
- for Ironic API tests, the `ironic-conductor-k8s`'s `enabled-hw-types` config
  option must be set to `fake`.

To run the Ironic API Tempest tests, run the following command, once the
`tempest` juju unit is in an active state:

    juju run -m openstack --wait=60m tempest/leader validate regex="ironic_tempest_plugin.tests.api..*"

## Other resources

- [tempest-k8s](https://charmhub.io/tempest-k8s) on Charmhub (more docs there)
