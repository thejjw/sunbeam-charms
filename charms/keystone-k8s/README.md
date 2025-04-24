# keystone-k8s

## Description

keystone-k8s is an operator to manage the Keystone identity service
on a Kubernetes based environment.

## Usage

### Deployment

keystone-k8s is deployed using below command:

    juju deploy keystone-k8s keystone --trust

Now connect the keystone operator to an existing database.

    juju relate mysql:database keystone:database

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions keystone`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

The charm supports the following relations. They are primarily of use to
developers:

* `identity-credentials`: Used by charms to obtain Keystone credentials without
  creating a service catalogue entry. Set 'username' only on the relation and
  Keystone will set defaults and return authentication details. Possible
  relation settings:

  * `username`: Username to be created.
  * `project`: Project (tenant) name to be created. Defaults to service's
               project.
  * `domain`: Keystone v3 domain the user will be created in. Defaults to the
              Default domain.

* `identity-service`: Used by API endpoints to request an entry in the Keystone
  service catalogue and the endpoint template catalogue.

  When a relation is established Keystone receives the following data from the
  requesting API endpoint:

  * `service_name`
  * `region`
  * `public_url`
  * `admin_url`
  * `internal_url`

  Keystone verifies that the requested service is supported (the list of
  supported services should remain updated). The following will occur for a
  supported service:

  1. an entry in the service catalogue is created
  1. an endpoint template is created
  1. an admin token is generated.

  The API endpoint receives the token and is informed of the ports that
  Keystone is listening on.

## OCI Images

The charm by default uses `ghcr.io/canonical/keystone:2025.1` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-keystone-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/charm-keystone-k8s/src/branch/main/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-keystone-k8s]: https://bugs.launchpad.net/charm-keystone-k8s/+filebug
