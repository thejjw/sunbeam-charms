# neutron-baremetal-switch-config-k8s

## Description

This charm allows conveying the necessary settings to the neutron-k8s charm,
in order for it to populate its `ml2_conf_networking_baremetal.ini` config
file.

## Usage

### Deployment

neutron-baremetal-switch-config-k8s is deployed using command below:

    juju deploy neutron-baremetal-switch-config-k8s neutron-baremetal-config

For instructions on how to build the charm and deploy / refresh it, check out
the [CONTRIBUTING.md][contributors-guide].

Now connect the neutron-baremetal-switch-config-k8s operator to the neutron-k8s
operator:

    juju relate neutron:baremetal-switch-config neutron-baremetal-config:switch-config

### Configuration

Baremetal switch configurations can be passed through Juju secrets. As an example,
let's consider the following `baremetal.conf` sample configuration file:

```ini
["nexus.example.net"]
driver = "netconf-openconfig"
device_params = "name:nexus"
switch_info = "nexus"
switch_id = "00:53:00:0a:0a:0a"
host = "nexus.example.net"
username = "user"
key_filename = "/etc/neutron/ssh_keys/nexus_sshkey"
```

Note that the configuration file references a `key_filename`. This key will
have to be included in the Juju secret. The Juju secret can be created with
the following command:

    juju add-secret baremetal-conf conf#file=./baremetal.conf nexus-sshkey#file=./nexus_sshkey
    juju grant-secret baremetal-conf neutron-baremetal-config

**NOTE**: The Juju secret keys must be lowercase letters and digits, at least
3 characters long, start with a letter, and not start or end with a hyphen.

Note the returned secret ID, it will be passed as a config option to the charm:

    juju config neutron-baremetal-config conf-secrets="secret_id"

**NOTE**: This configuration option is a comma-separated list, meaning that
the charm can be used to handle multiple switch configurations. See the
`config.yaml` file for the full list of options, along with their descriptions
and default values. See the [Juju documentation][juju-docs-config-apps] for
details on configuring applications.

## Relations

neutron-baremetal-switch-config-k8s has the following optional relations:

- `logging`: To send logs to Loki.

The charm provides the following relation:

- `switch-config`: Provides the baremetal switch configuration, needed to
  populate OpenStack Neutron's `ml2_conf_networking_baremetal.ini` file.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-neutron-baremetal-switch-config-k8s].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/neutron-baremetal-switch-config-k8s/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-neutron-baremetal-switch-config-k8s]: https://bugs.launchpad.net/sunbeam-charms/+filebug
