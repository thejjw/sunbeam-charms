# cinder-volume-stx

## Description

The cinder-volume-stx is an operator to manage the Cinder service
integration with Stx backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-stx is deployed using below command:

    juju deploy cinder-volume-stx

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-stx:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
