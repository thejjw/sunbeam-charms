# cinder-volume-toyouacs5000

## Description

The cinder-volume-toyouacs5000 is an operator to manage the Cinder service
integration with Acs5000 FC backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-toyouacs5000 is deployed using below command:

    juju deploy cinder-volume-toyouacs5000

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-toyouacs5000:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
