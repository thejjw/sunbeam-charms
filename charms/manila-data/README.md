# manila-data

## Description

The `manila-data` is an operator to manage OpenStack Manila data service in a
Snap-based deployment.

## Usage

### Deployment

Deploy the charm and bind the `storage` endpoint to the space used to reach
the share exports:

    juju deploy manila-data --bind "storage=<storage-space>"

Then integrate it with the database, messaging and identity operators:

    juju integrate mysql:database manila-data:database
    juju integrate rabbitmq:amqp manila-data:amqp
    juju integrate keystone:identity-credentials manila-data:identity-credentials

For instructions on building the charm and deploying or refreshing a local
build, see [CONTRIBUTING.md][contributors-guide].

### Network bindings

- `storage`: The unit's address on this binding is passed to Manila as the
  data node access IP. The share backends will grant this address access to
  mounted shares during host-assisted migration. The IP address must be able
  to reach the share exports (for example Ceph NFS). If unbound, the default
  space is used.

### Snap interfaces

The charm connects the following snap plugs, which are needed to mount shares
during host-assisted share migration:

- `nfs-mount`
- `mount-observe`

### Configuration

See the `config` section of `charmcraft.yaml` for the full list of options and
their defaults, and the [Juju documentation][juju-docs-config-apps] for how to
configure applications.

- `snap-channel`: Snap channel to track.
- `debug`: Enable debug logging.
- `enable-telemetry-notifications`: Send notifications to telemetry.

### Actions

Run `juju actions manila-data` to list the [actions][juju-docs-actions]
supported by the charm.

- `refresh-snap`: Refresh the snap to the latest revision on the configured
  channel. The snap is held after installation, so updates are only applied
  through this action.

## Relations

Required:

- `amqp`: Connect to RabbitMQ.
- `database`: Connect to MySQL.
- `identity-credentials`: Connect to Keystone.

Optional:

- `logging`: Send logs to Loki.
- `receive-ca-cert`: Receive CA certificates.
- `tracing`: Send traces to a tracing backend.

## Contributing

See the [Juju SDK docs](https://juju.is/docs/sdk) for charm development
guidelines, and [CONTRIBUTING.md][contributors-guide] for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-manila-data].

<!-- LINKS -->

[contributors-guide]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/manila-data/CONTRIBUTING.md
[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://documentation.ubuntu.com/juju/3.6/reference/configuration/#application-configuration
[lp-bugs-charm-manila-data]: https://bugs.launchpad.net/sunbeam-charms/+filebug
