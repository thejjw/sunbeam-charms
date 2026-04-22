# cinder-volume-opene

## Description

The cinder-volume-opene is an operator to manage the Cinder service
integration with Jovian iSCSI backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-opene is deployed using below command:

    juju deploy cinder-volume-opene

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-opene:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
