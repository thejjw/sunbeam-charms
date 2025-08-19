# cinder-volume-hitachi – Contributing Guide

Thank you for your interest in improving the **cinder-volume‑hitachi** charm!
This document explains how to get a development environment up and
running, how the code is structured, and how to run tests and build the
charm.

---

## Developing

### 1. Set up a virtualenv

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

*The same tooling is used by all Sunbeam charms, so you may reuse an
existing environment if you already work on other charms.*

### 2. Code style & linting

* **black** – auto‑formats Python code.
* **ruff** – fast linter (runs `flake8`, `isort`, etc.).
* **mypy** – optional static typing; the CI gate enforces “strict‑optional”.

Run all checks locally:

```bash
tox -e pep8
```

---

## Code overview

The charm is built with the
[**Charmed Operator Framework**](https://juju.is/docs/sdk) and the
`ops_sunbeam` helper library.

* **`CinderVolumeHitachiOperatorCharm`** (in `src/charm.py`) extends
  `OSSubordinateBaseOperatorCharm` and publishes a *single* relation –
  **`cinder-volume`** – to inject the Hitachi backend stanza into
   `cinder.conf` within the principal application (usually the
  `cinder-volume` snap).
* Unlike the Ceph variant, this charm **does not** speak to Ceph, RabbitMQ
  or a database; all driver parameters are supplied via charm **config**.
* Configuration is rendered through a Jinja2 template shipped inside the
  `cinder-volume` snap, so the charm’s responsibility is limited to
  mapping config keys and triggering a service restart.

---

## Intended use case

Deploy the charm side‑by‑side with the principal **`cinder-volume`** charm
in a Sunbeam (or other Juju‑managed) Kubernetes model to attach a Hitachi
VSP storage array as an additional Cinder backend.

Example:

```bash
juju deploy cinder-volume          # principal
juju deploy cinder-volume-hitachi  --trust
juju relate cinder-volume-hitachi cinder-volume
```

---

## Roadmap

* Add support for multiple VSP arrays via application‑scoped units.
* Implement `volume‑group` replication helpers.
* Provide a `hitachi-storage-check` action for quick health status.

Feel free to propose new features or improvements via GitHub / Launchpad
issues or pull requests.

---

## Testing

The Python operator framework includes a harness for unit‑testing charm
logic without a full Juju deployment.

```bash
tox -e py3       # run unit tests
```

Integration tests with microk8s + Sunbeam can be run via the `integration`
Tox environment (requires a local LXD remote and a VSP simulator or test
array credentials).

---

## Building & deploying locally

This project uses **Tox** to wrap `charmcraft`.

```bash
tox -e build          # creates ./cinder-volume-hitachi.charm
juju deploy ./cinder-volume-hitachi.charm --trust
```

Happy hacking! 🙂

