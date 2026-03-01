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
    k8s_container,
    peer_relation,
)

CHARM_ROOT = Path(__file__).parents[2]


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
def container():
    """A connectable designate-bind container."""
    return k8s_container("designate-bind")


@pytest.fixture()
def complete_state(container):
    """Full state with leader, peer relation, and container."""
    return testing.State(
        leader=True,
        relations=[peer_relation()],
        containers=[container],
    )
