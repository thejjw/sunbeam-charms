# Contributing

## Using functional tests

The functional tests can be run locally.
You will need microk8s configured, and juju bootstrapped on it.
See ./zuul.d/zuul.yaml variables for the current supported versions
of microk8s and juju as used for functional tests in CI.

Ensure microk8s is the active controller on juju,
and run the tox target, with the appropriate zaza options
to select the test config you want to run:

```
tox -e func -- --smoke --test-directory=tests/core
```

See the tox.ini file for specifically what is being run.

After running this, the juju model and the tox virtual environment will remain,
so you can activate the virtual environment,
and directly run zaza commands to re-run tests for debugging, etc.

For example:

```
source .tox/func/bin/activate

# fix the path to pick up the local test modules
export PYTHONPATH="tests/local:$PYTHONPATH"

# run a zaza command directly
functest-test -m zaza-1537f907eca2 -t zaza.sunbeam.charm_tests.tempest_k8s.tests.TempestK8sTest --test-directory tests/core
```

Note that you can run `juju models` to see the generated name for the zaza model used.
The `PYTHONPATH` must also be updated to pick up the tests from `./tests/local/*`,
which is where the zaza tests are specifically for sunbeam-charms.
