# cinder-volume-zadara

## Description

The cinder-volume-zadara is an operator to manage the Cinder service
integration with ZadaraVPSA iSCSI backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-zadara is deployed using below command:

    juju deploy cinder-volume-zadara

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-zadara:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
