# cinder-volume-dellsc

## Description

`cinder-volume-dellsc` is a **subordinate** Juju charm that integrates the
OpenStack **Cinder** volume service with Dell Storage Center (SC Series)
block‑storage arrays. It renders a dedicated back‑end stanza in *cinder.conf*
(namespace `dellsc.<backend-name>`), manages driver options, and restarts the
`snap.cinder-volume.*` service when configuration changes.

---

## Usage

### Deployment

Deploy the subordinate charm and grant cloud credentials:

```bash
juju deploy cinder-volume-dellsc --trust
```

Relate it to the principal Cinder Volume application (usually called
`cinder-volume` in the Sunbeam model):

```bash
juju relate cinder-volume cinder-volume-dellsc:cinder-volume
```

### Configuration

Create a Juju secret for the Dell Storage Center credentials:

```bash
juju add-secret dellsc-config \
  primary-username=admin \
  primary-password=supersecret \
  secondary-username=admin2 \
  secondary-password=secret2
```

Set the mandatory connection details for your array:

```bash
juju config cinder-volume-dellsc \
  san-ip=10.0.0.50 \
  dellsc-config-secret=secret:dellsc-config \
  dell-sc-ssn=64702 \
  protocol=fc \
  volume-backend-name=dellsc01
```

To enable dual DSM, add a secondary IP:

```bash
juju config cinder-volume-dellsc \
  secondary-san-ip=10.0.0.51 \
  secondary-sc-api-port=3033
```

See **Configuration Reference** below for all available options.

### Actions

This charm currently has no Juju actions.

---

## Configuration Reference

Below is a subset of the most commonly used options. Consult
`charmcraft.yaml` for the exhaustive list.

| Option                             | Type    | Default | Description                                               |
| ---------------------------------- | ------- | ------- | --------------------------------------------------------- |
| `san-ip`                           | string  | –       | Management IP / hostname of the Storage Center.          |
| `dellsc-config-secret`             | secret  | –       | Secret with `primary-*` and `secondary-*` keys.           |
| `san-credentials-secret`           | secret  | –       | Legacy secret with `username`/`password` keys.            |
| `dell-sc-ssn`                       | int     | –       | Storage Center system serial number.                      |
| `protocol`                         | string  | `fc`    | `fc` or `iscsi` driver variant.                           |
| `dell-sc-api-port`                 | int     | 3033    | Dell Storage Center API port.                             |
| `dell-sc-server-folder`            | string  | openstack | Server folder name on the Storage Center.              |
| `dell-sc-volume-folder`            | string  | openstack | Volume folder name on the Storage Center.              |
| `dell-sc-verify-cert`              | boolean | false   | Verify HTTPS certificates for the Storage Center API.     |
| `secondary-san-ip`                 | string  | –       | Secondary DSM/Storage Center IP for dual DSM.             |
| `secondary-san-credentials-secret` | secret  | –       | Optional secret with `username`/`password` keys.          |

---

## Driver extra specs

The Dell SC Cinder driver supports volume type extra specs for storage and
replay profiles, replication (including Live Volume), QoS, and data reduction
profiles. Configure these via `openstack volume type set --property ...`.
Refer to the upstream Dell SC driver documentation for the full list.

---

## Relations

`cinder-volume-dellsc` requires one relation:

| Relation        | Interface       | Scope     | Notes                                                                  |
| --------------- | --------------- | --------- | ---------------------------------------------------------------------- |
| `cinder-volume` | `cinder-volume` | container | Connects to the principal charm that runs the `cinder-volume` service. |

The charm also declares an optional `tracing` relation for distributed
tracing (Jaeger / OpenTelemetry).

---

## Contributing

Contributions are welcome! Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for best‑practice guidelines and the
project [CONTRIBUTING](https://opendev.org/openstack/sunbeam-charms/src/branch/main/CONTRIBUTING.md)
file for developer workflow.

## Bugs

Report bugs at the charm collection’s Launchpad:
[https://bugs.launchpad.net/sunbeam-charms](https://bugs.launchpad.net/sunbeam-charms).
