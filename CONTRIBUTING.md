# Contributing

## Using functional tests

The functional tests can be run locally.
You will need k8s configured, and juju bootstrapped on it.
See ./zuul.d/zuul.yaml variables for the current supported versions
of k8s and juju as used for functional tests in CI.

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

## Running functional tests using ansible playbooks

Install ansible

```
sudo add-apt-repository --yes --update ppa:ansible/ansible
sudo apt install ansible -y
```

Clone the following projects

```
git clone https://opendev.org/openstack/charm-zuul-jobs.git
git clone https://opendev.org/zuul/zuul-jobs.git
git clone https://opendev.org/openstack/sunbeam-charms.git
```

Render the bundles. The script picks any .charm files in
sunbeam-charms directory and will use them instead of
from charmhub.io

```
cd sunbeam-charms
python3 render_bundles.py
```

Add variables file to ansible. Look at zuul.d/zuul.yaml or default
vars from the roles folder for the list of variables that can be
modified.

```
# Variables from zuul
zuul:
  buildset: 1
  branch: main
  project:
    src_dir: /home/ubuntu/sunbeam-charms
  executor:
    work_root: /home/ubuntu

# Variables from jobs
ansible_user: ubuntu

juju_channel: 3.6/stable
juju_classic_mode: false
env_type: k8s
k8s_channel: 1.32-classic/stable
k8s_classic_mode: true
charmcraft_channel: 3.x/stable
nftables_enabled: false
skip_charm_download: true
primary_controller: false
```

Run func tests using ansible

```
ANSIBLE_ROLES_PATH=/home/ubuntu/sunbeam-charms/roles:/home/ubuntu/charm-zuul-jobs/roles:/home/ubuntu/zuul-jobs/roles \
  ansible-playbook -i 127.0.0.1, --connection=local -e "@vars.yaml" playbooks/zaza-func-test.yaml
```

If playbooks are modified just to deploy the pre-requisite environment, func tests can run using
the following tox command

```
/home/ubuntu/.local/tox/bin/tox -e func -- --smoke --test-directory=tests/openstack
```

Rerun functest-* commands using following commands

```
source .tox/func/bin/activate
PYTHONPATH=/home/ubuntu/sunbeam-charms/tests/local:$PYTHONPATH functest-configure -m MODEL --test-directory tests/openstack
PYTHONPATH=/home/ubuntu/sunbeam-charms/tests/local:$PYTHONPATH functest-test -m MODEL --test-directory tests/openstack
```
