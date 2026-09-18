# cinder-volume-dellpowerflex

## Description

The cinder-volume-dellpowerflex is an operator to manage the Cinder service
integration with Dell PowerFlex backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-dellpowerflex is deployed using below command:

    juju deploy cinder-volume-dellpowerflex

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-dellpowerflex:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
