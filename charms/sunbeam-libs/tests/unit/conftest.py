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

"""Shared fixtures for sunbeam-libs unit tests."""

from pathlib import (
    Path,
)

import charm
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    k8s_container,
)

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture()
def ctx():
    """Create a testing.Context for SunbeamLibsCharm."""
    return testing.Context(charm.SunbeamLibsCharm, charm_root=CHARM_ROOT)


@pytest.fixture()
def container():
    """A connectable placeholder container."""
    return k8s_container("placeholder")
