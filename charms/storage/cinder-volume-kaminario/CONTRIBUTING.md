# cinder-volume-kaminario

## Developing

Create and activate a virtualenv with the development requirements:

    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Code overview

Get familiarised with [Charmed Operator Framework](https://juju.is/docs/sdk)
and [Sunbeam documentation](sunbeam-docs).

This charm uses the `ops_sunbeam` library and extends
`OSCinderVolumeDriverOperatorCharm` — a subordinate base class that delivers
backend configuration to the principal `cinder-volume` snap.

## Intended use case

Deploy alongside the principal **`cinder-volume`** charm to attach a
Kaminario storage array as a Cinder
backend in a Sunbeam OpenStack deployment.

```bash
juju deploy cinder-volume
juju deploy cinder-volume-kaminario --trust
juju relate cinder-volume-kaminario cinder-volume
```

## Testing

    tox -e py3

## Building

    tox -e build

<!-- LINKS -->

[sunbeam-docs]: https://opendev.org/openstack/sunbeam-charms/src/branch/main/README.md
