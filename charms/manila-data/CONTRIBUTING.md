# manila-data

## Code overview

The charm extends `OSBaseOperatorCharmSnap` from the `ops_sunbeam` library and
manages the `manila-data` snap. See the [Snap's documentation][snap-manila-docs]
and the [Juju SDK docs][juju-sdk] for background.

On each configuration pass the charm:

- Connects the snap's `nfs-mount` and `mount-observe` plugs, which are needed
  to mount shares during host-assisted share migration.
- Builds the snap configuration from the `database`, `amqp` and
  `identity-credentials` relations and the charm config.
- Passes the unit's `storage` binding address to the snap as
  `settings.data-node-access-ips`, the address share backends grant access to.

The snap renders the Manila configuration and runs the `manila-data` service.

## Development

All commands run through `tox` from the repository root. From this directory,
pass `--root ../../`.

Run the unit tests:

    tox --root ../../ -e py3 -- manila-data

Check and apply code formatting (runs across all charms):

    tox --root ../../ -e pep8
    tox --root ../../ -e fmt

Regenerate `uv.lock`, upgrading all dependencies to their latest allowed
versions (for example after changing `pyproject.toml`):

    tox --root ../../ -e lock -- manila-data

## Building and deploying

Build the charm (requires `charmcraft`). The shared libraries are copied in
before packing, and the result is written to `manila-data.charm` at the
repository root:

    tox --root ../../ -e build -- manila-data

Deploy the local build from the repository root:

    juju deploy ./manila-data.charm manila-data --bind "storage=<storage-space>"

Refresh an existing deployment with a local build:

    juju refresh manila-data --path ./manila-data.charm

See the repository [CONTRIBUTING.md][repo-contributing] for functional tests.

<!-- LINKS -->

[juju-sdk]: https://juju.is/docs/sdk
[repo-contributing]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/CONTRIBUTING.md
[snap-manila-docs]: https://github.com/canonical/snap-manila-data/blob/main/README.md
