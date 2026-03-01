# Copyright 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared fixtures for neutron-generic-switch-config-k8s unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]

_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = %(device_type)s
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
"""


def get_sample_config(
    name: str, device_type: str, with_key: bool = True
) -> str:
    """Build a sample genericswitch config string."""
    config = _SAMPLE_CONFIG % {"name": name, "device_type": device_type}
    if with_key:
        config = config + "\nkey_file = /etc/neutron/sshkeys/%s-key" % name
    return config


ARISTA_CONFIG = get_sample_config("arista", "netmiko_arista_eos")
ARISTA_CONFIG_NO_KEY = get_sample_config(
    "arista", "netmiko_arista_eos", with_key=False
)


@pytest.fixture()
def ctx():
    """Create a testing.Context for NeutronGenericSwitchConfigCharm."""
    return testing.Context(
        charm.NeutronGenericSwitchConfigCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def switch_config_relation():
    """A switch-config relation."""
    return testing.Relation(
        "switch-config", interface="switch-config", remote_app_name="neutron"
    )


@pytest.fixture()
def valid_secret():
    """A valid app-owned secret with generic switch config and SSH key."""
    return testing.Secret(
        tracked_content={
            "conf": ARISTA_CONFIG,
            "arista-key": "foo",
        },
        owner="app",
    )


@pytest.fixture()
def valid_state(valid_secret, switch_config_relation):
    """Full state: leader, valid secret, switch-config relation."""
    return testing.State(
        leader=True,
        config={"conf-secrets": valid_secret.id},
        relations=[switch_config_relation],
        secrets=[valid_secret],
    )
