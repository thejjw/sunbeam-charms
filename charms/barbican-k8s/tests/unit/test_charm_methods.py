#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
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

"""Focused unit tests for Barbican readiness publication helpers."""

from pathlib import (
    Path,
)
from unittest import (
    mock,
)
from unittest.mock import (
    MagicMock,
)

import charm
import ops_sunbeam.test_utils as test_utils
import pytest

CHARM_ROOT = Path(__file__).parents[2]


@pytest.fixture()
def harness():
    """Provide a harness for BarbicanVaultOperatorCharm."""
    charmcraft = CHARM_ROOT / "charmcraft.yaml"
    h = test_utils.get_harness(
        charm.BarbicanVaultOperatorCharm,
        charm_metadata=charmcraft.read_text(),
    )
    yield h
    h.cleanup()


class TestServiceReadinessProvider:
    """Tests for the Barbican service-ready provider helpers."""

    def test_handle_readiness_request_sets_current_status(self, harness):
        """Readiness requests should publish the current bootstrapped state."""
        harness.begin()
        rel_id = harness.add_relation(
            "barbican-service", "openstack-hypervisor"
        )
        relation = harness.model.get_relation("barbican-service", rel_id)
        harness.charm.svc_ready_handler.interface.set_service_status = (
            MagicMock()
        )

        with mock.patch.object(
            harness.charm, "bootstrapped", return_value=True
        ):
            harness.charm.handle_readiness_request_from_event(
                MagicMock(relation=relation)
            )

        harness.charm.svc_ready_handler.interface.set_service_status.assert_called_once_with(
            relation, True
        )

    def test_set_readiness_on_related_units_converges_false(self, harness):
        """Readiness refresh should also publish not-ready states."""
        harness.begin()
        rel_id = harness.add_relation(
            "barbican-service", "openstack-hypervisor"
        )
        relation = harness.model.get_relation("barbican-service", rel_id)
        harness.charm.svc_ready_handler.interface.set_service_status = (
            MagicMock()
        )

        with mock.patch.object(
            harness.charm, "bootstrapped", return_value=False
        ):
            harness.charm.set_readiness_on_related_units()

        harness.charm.svc_ready_handler.interface.set_service_status.assert_called_once_with(
            relation, False
        )
