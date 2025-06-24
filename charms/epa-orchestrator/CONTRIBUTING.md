# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

You can create an environment for development with `tox`:

```shell
virtualenv venv
source venv/bin/activate
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e fmt
tox run -e linters
tox run -e pep8
tox -e py3 -- epa-orchestrator
```

## Smoke testing
```shell
tox  -e func -- --smoke --test-directory=/root/sunbeam-charms/tests/machine/
```

## Build the charm

Build the charm in this git repository using:

```shell
tox -e build -- epa-orchestrator
```
