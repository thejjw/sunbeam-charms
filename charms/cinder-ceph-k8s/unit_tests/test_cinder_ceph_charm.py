#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd.
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

import unittest
import sys

sys.path.append("lib")  # noqa
sys.path.append("src")  # noqa

from ops.testing import Harness

import charm


class _CinderCephVictoriaOperatorCharm(charm.CinderCephVictoriaOperatorCharm):
    def __init__(self, framework):
        self.seen_events = []
        self.render_calls = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def renderer(
        self,
        containers,
        container_configs,
        template_dir,
        openstack_release,
        adapters,
    ):
        self.render_calls.append(
            (
                containers,
                container_configs,
                template_dir,
                openstack_release,
                adapters,
            )
        )

    def _on_service_pebble_ready(self, event):
        super()._on_service_pebble_ready(event)
        self._log_event(event)


class TestCinderCephOperatorCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(_CinderCephVictoriaOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin_with_initial_hooks()

    def test_amqp_relation(self):
        # Fake out the ceph relation for AMQP testing
        self.harness.charm.ceph.interface._stored.pools_available = True
        # Initial state - handlers will be incomplete
        self.assertFalse(self.harness.charm.relation_handlers_ready())
        # Add relation = handler will still be incomplete until
        # data is provided
        amqp_rel = self.harness.add_relation("amqp", "rabbitmq")
        self.harness.add_relation_unit(amqp_rel, "rabbitmq/0")
        self.assertFalse(self.harness.charm.relation_handlers_ready())
        # Add app data to relation
        self.harness.update_relation_data(
            amqp_rel,
            "rabbitmq",
            {
                "password": "foobar",
                "hostname": "rabbitmq.endpoint.kubernetes.local",
            },
        )
        self.assertTrue(self.harness.charm.relation_handlers_ready())
        # Perform some basic validation that the interface data
        # is correctly set
        self.assertEqual(self.harness.charm.amqp.interface.username, "cinder")
        self.assertEqual(self.harness.charm.amqp.interface.vhost, "openstack")
        self.assertEqual(self.harness.charm.amqp.interface.password, "foobar")
        self.assertEqual(
            self.harness.charm.amqp.interface.hostname,
            "rabbitmq.endpoint.kubernetes.local",
        )
        # Remove the relation which should result in relation
        # handlers returing to an un-ready state.
        self.harness.remove_relation(amqp_rel)
        self.assertFalse(self.harness.charm.relation_handlers_ready())
