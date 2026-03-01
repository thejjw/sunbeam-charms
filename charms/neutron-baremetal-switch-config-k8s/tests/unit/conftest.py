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

"""Shared fixtures for neutron-baremetal-switch-config-k8s unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)

CHARM_ROOT = Path(__file__).parents[2]

_SAMPLE_CONFIG = """driver = netconf-openconfig
device_params = name:nexus
switch_info = nexus
switch_id = 00:53:00:0a:0a:0a
host = nexus.example.net
username = user
"""

NEXUS_SAMPLE_CONFIG = "[nexus.example.net]\n" + _SAMPLE_CONFIG
KEY_LINE = "key_filename = /etc/neutron/sshkeys/nexus-sshkey"


@pytest.fixture()
def ctx():
    """Create a testing.Context for NeutronBaremetalSwitchConfigCharm."""
    return testing.Context(
        charm.NeutronBaremetalSwitchConfigCharm, charm_root=CHARM_ROOT
    )


@pytest.fixture()
def switch_config_relation():
    """A switch-config relation."""
    return testing.Relation(
        "switch-config", interface="switch-config", remote_app_name="neutron"
    )


@pytest.fixture()
def valid_secret():
    """A valid app-owned secret with baremetal switch config."""
    return testing.Secret(
        tracked_content={"conf": NEXUS_SAMPLE_CONFIG},
        owner="app",
    )


@pytest.fixture()
def valid_secret_with_key():
    """A valid app-owned secret with config and SSH key."""
    return testing.Secret(
        tracked_content={
            "conf": "\n".join([NEXUS_SAMPLE_CONFIG, KEY_LINE]),
            "nexus-sshkey": "foo",
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


@pytest.fixture()
def valid_state_with_key(valid_secret_with_key, switch_config_relation):
    """Full state: leader, valid secret with SSH key, switch-config relation."""
    return testing.State(
        leader=True,
        config={"conf-secrets": valid_secret_with_key.id},
        relations=[switch_config_relation],
        secrets=[valid_secret_with_key],
    )
