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

"""Shared fixtures for designate-bind-k8s unit tests."""

from pathlib import (
    Path,
)
from unittest.mock import (
    MagicMock,
    patch,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]
ROCK_BASE_PLAN = {
    "services": {
        "dns-server": {
            "override": "replace",
            "command": "/usr/sbin/named [ -g ]",
            "startup": "enabled",
        },
    },
    "checks": {
        "config": {
            "override": "replace",
            "exec": {
                "command": "/usr/bin/named-checkconf",
            },
            "service-context": "dns-server",
        },
    },
}


@pytest.fixture(autouse=True)
def _mock_lightkube():
    """Mock lightkube Client so tests don't need k8s credentials."""
    with patch(
        "ops_sunbeam.k8s_resource_handlers.Client",
        return_value=MagicMock(),
    ):
        yield


@pytest.fixture()
def ctx():
    """Create a testing.Context for BindOperatorCharm."""
    return testing.Context(charm.BindOperatorCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def container(tmp_path):
    """A connectable designate-bind container using the rock base plan."""
    cache_dir = tmp_path / "cache"
    run_dir = tmp_path / "run"
    cache_dir.mkdir()
    run_dir.mkdir()
    dynamic_zones = cache_dir / "_default.nzd"
    dynamic_zones.write_text("root-owned")
    dynamic_zones.chmod(0o600)
    return testing.Container(
        name="designate-bind",
        can_connect=True,
        _base_plan=ROCK_BASE_PLAN,
        service_statuses={
            "dns-server": testing.pebble.ServiceStatus.ACTIVE,
        },
        mounts={
            "bind-cache": testing.Mount(
                location="/var/cache/bind", source=cache_dir
            ),
            "bind-run": testing.Mount(location="/run", source=run_dir),
        },
    )


@pytest.fixture()
def complete_state(container):
    """Full state with leader, peer relation, and container."""
    return testing.State(
        leader=True,
        relations=[peer_relation()],
        containers=[container],
    )


def dns_backend_relation() -> testing.Relation:
    """dns-backend relation (bind is the provider side)."""
    return testing.Relation(
        endpoint="dns-backend",
        remote_app_name="designate",
        remote_app_data={},
        remote_units_data={0: {}},
    )


@pytest.fixture()
def complete_state_with_dns_backend(container):
    """State with leader, peer, dns-backend relation, and container."""
    return testing.State(
        leader=True,
        relations=[peer_relation(), dns_backend_relation()],
        containers=[container],
    )


@pytest.fixture()
def non_leader_state_with_dns_backend(container):
    """State with non-leader, peer, dns-backend relation, and container."""
    return testing.State(
        leader=False,
        relations=[peer_relation(), dns_backend_relation()],
        containers=[container],
    )
