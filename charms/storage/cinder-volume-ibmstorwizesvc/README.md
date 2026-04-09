# cinder-volume-ibmstorwizesvc

## Description

The cinder-volume-ibmstorwizesvc is an operator to manage the Cinder service
integration with StorwizeSVC FC backend on a snap based deployment.

## Usage

### Deployment

cinder-volume-ibmstorwizesvc is deployed using below command:

    juju deploy cinder-volume-ibmstorwizesvc

Now connect the application to cinder-volume:

    juju relate cinder-volume:cinder-volume cinder-volume-ibmstorwizesvc:cinder-volume

### Configuration

See file `config.yaml` for options. See [Juju documentation][juju-docs-config-apps].

## Relations

`cinder-volume`: Required relation to Cinder service

## Bugs

Report bugs on [Launchpad][lp-bugs-sunbeam-charms].

[lp-bugs-sunbeam-charms]: https://bugs.launchpad.net/sunbeam-charms
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
