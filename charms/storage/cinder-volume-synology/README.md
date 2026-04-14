# cinder-volume-synology

## Description

The cinder-volume-synology is an operator to manage the Cinder service
integration with Syno iSCSI backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-synology is deployed using below command:

    juju deploy cinder-volume-synology

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-synology:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
