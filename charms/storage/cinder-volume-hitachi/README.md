# cinder-volume-hitachi

## Description

`cinder-volume-hitachi` is a **subordinate** Juju charm that integrates the
OpenStack **Cinder** volume service with Hitachi VSP block‑storage arrays.
It renders a dedicated back‑end stanza in *cinder.conf* (namespace
`hitachi.<backend‑name>`), manages driver options, and restarts the
`snap.cinder-volume.*` service when the configuration changes.

Supported VSP models include the E‑series, F/G/N‑series, VSP 5000/5600, and
VSP E1090 (see the upstream driver docs for the full list).

---

## Usage

### Deployment

Deploy the subordinate charm and grant cloud credentials:

```bash
juju deploy cinder-volume-hitachi --trust
```

Relate it to the principal Cinder Volume application (usually called
`cinder-volume` in the Sunbeam model):

```bash
juju relate cinder-volume cinder-volume-hitachi:cinder-volume
```

### Configuration

Set the mandatory connection details for your array:

```bash
juju config cinder-volume-hitachi \
  san-ip=10.0.0.50 \
  san-login=svcuser \
  san-password=supersecret \
  hitachi-storage-id=45000 \
  hitachi-pools=DP_POOL_01 \
  volume-backend-name=vsp350
```

See **Configuration Reference** below for all available options.

### Actions

This charm currently has no Juju actions.

---

## Configuration Reference

Below is a subset of the most commonly used options. Consult
`charmcraft.yaml` for the exhaustive list (more than 70 driver knobs).

| Option                                                                 | Type   | Default  | Description                                                          |
| ---------------------------------------------------------------------- | ------ | -------- | -------------------------------------------------------------------- |
| `san-ip`                                                               | string | –        | Management IP / hostname of the VSP REST interface. **Required**     |
| `san-login`                                                            | string | –        | Array user with *Storage Administrator* role. **Required**           |
| `san-password`                                                         | string | –        | Password for the above user. **Required** (stored as Juju secret)    |
| `hitachi-storage-id`                                                   | string | –        | Serial / Product ID of the array (e.g. `45000`). **Required**        |
| `hitachi-pools`                                                        | string | –        | Comma‑separated DP pool list where volumes are created. **Required** |
| `protocol`                                                             | string | `FC`     | Use `FC` or `iSCSI` driver variant.                                  |
| `volume-backend-name`                                                  | string | app name | Logical backend name advertised to Cinder.                           |
| `backend-availability-zone`                                            | string | –        | AZ override for this backend.                                        |
| `hitachi-target-ports`                                                 | string | –        | Restrict host mappings to specific front‑end ports.                  |
| *… plus all advanced copy, replication, QoS, HORCM and GAD settings …* |        |          |                                                                      |

---

## Relations

`cinder-volume-hitachi` requires one relation:

| Relation        | Interface       | Scope     | Notes                                                                  |
| --------------- | --------------- | --------- | ---------------------------------------------------------------------- |
| `cinder-volume` | `cinder-volume` | container | Connects to the principal charm that runs the `cinder-volume` service. |

The charm also declares an optional `tracing` relation for distributed
tracing (Jaeger / OpenTelemetry).

---

## Contributing

Contributions are welcome!  Please see the
[Juju SDK docs](https://juju.is/docs/sdk) for best‑practice guidelines and the
project [CONTRIBUTING](https://opendev.org/openstack/sunbeam-charms/src/branch/main/CONTRIBUTING.md)
file for developer workflow.

## Bugs

Report bugs at the charm collection’s Launchpad:
[https://bugs.launchpad.net/sunbeam-charms](https://bugs.launchpad.net/sunbeam-charms).

