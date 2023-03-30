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

"""Tests for Openstack hypervisor charm."""

import unittest
import mock
import ops_sunbeam.test_utils as test_utils

import ops.testing
from ops.testing import Harness

import charm


class _HypervisorOperatorCharm(charm.HypervisorOperatorCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)

class TestCharm(test_utils.CharmTestCase):
    PATCHES = ['subprocess']
    def setUp(self):
        """Setup OpenStack Hypervisor tests."""
        super().setUp(charm, self.PATCHES)
        with open("config.yaml", "r") as f:
            config_data = f.read()
        self.harness = test_utils.get_harness(
            _HypervisorOperatorCharm,
            container_calls=self.container_calls,
            charm_config=config_data
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.set_leader()
        self.harness.update_config({'snap-channel': 'essex/stable'})
        test_utils.add_all_relations(self.harness)
        self.subprocess.check_call.assert_any_call(
            ['snap', 'install', 'openstack-hypervisor', '--channel', 'essex/stable'])
