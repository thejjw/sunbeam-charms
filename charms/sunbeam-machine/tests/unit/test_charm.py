# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for Sunbeam Machine charm."""

import ops_sunbeam.test_utils as test_utils

import charm


class _SunbeamMachineCharm(charm.SunbeamMachineCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    PATCHES = []

    def setUp(self):
        """Setup Sunbeam machine tests."""
        super().setUp(charm, self.PATCHES)
        with open("config.yaml", "r") as f:
            config_data = f.read()
        self.harness = test_utils.get_harness(
            _SunbeamMachineCharm,
            container_calls=self.container_calls,
            charm_config=config_data,
        )
        self.addCleanup(self.harness.cleanup)

    def test_initial(self):
        self.harness.begin_with_initial_hooks()
        self.assertTrue(self.harness.charm.bootstrapped())
