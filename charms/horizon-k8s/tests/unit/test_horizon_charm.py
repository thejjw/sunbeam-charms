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

"""Unit tests for Horizon operator."""

import json
from unittest.mock import (
    MagicMock,
    Mock,
)

import charm
import ops_sunbeam.test_utils as test_utils


class _HorizonOperatorCharm(charm.HorizonOperatorCharm):
    """Test Operator Charm for Horizon Operator."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        return "dashboard.juju"


class TestHorizonOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Horizon Operator."""

    PATCHES = []

    def setUp(self):
        """Setup environment for unit test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _HorizonOperatorCharm, container_calls=self.container_calls
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequiresEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def add_trusted_dashboard_relation(self) -> int:
        """Add trusted-dashboard relation."""
        rel_id = self.harness.add_relation("trusted-dashboard", "keystone")
        self.harness.add_relation_unit(rel_id, "keystone/0")
        self.harness.update_relation_data(
            rel_id, "keystone/0", {"ingress-address": "10.0.0.11"}
        )
        self.harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "federated-providers": json.dumps(
                    [
                        {
                            "name": "hydra",
                            "protocol": "openid",
                            "description": "Hydra",
                        }
                    ]
                )
            },
        )
        return rel_id

    def add_identity_ops_relation(self, harness):
        """Add identity resource relation."""
        self.harness.charm.set_tempest_ready = Mock()
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.id_ops.callback_f = Mock()
        harness.charm.id_ops.list_regions = Mock(
            return_value=["RegionOne", "SecondRegion"],
        )
        # Only show the list_endpoint ops for simplicity
        harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "response": json.dumps(
                    {
                        "id": "test-request-id",
                        "tag": "horizon_list_regions",
                        "ops": [
                            {
                                "name": "some_other_ops",
                                "return-code": 0,
                                "value": "",
                            },
                            {
                                "name": "list_regions",
                                "return-code": 0,
                                "value": [
                                    "RegionOne",
                                    "SecondRegion",
                                ],
                            },
                        ],
                    }
                )
            },
        )
        return rel_id

    def test_all_relations(self):
        """Test all integrations for Operator."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        self.add_identity_ops_relation(self.harness)
        test_utils.add_all_relations(self.harness)
        test_utils.add_complete_ingress_relation(self.harness)

        self.harness.set_leader()
        rel_id = self.add_trusted_dashboard_relation()
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.unit.app.name
        )
        self.assertEqual(
            rel_data,
            {"dashboard-url": "http://public-url/auth/websso/"},
        )
        setup_cmds = [
            ["a2dissite", "000-default"],
            ["a2disconf", "openstack-dashboard"],
            ["a2ensite", "wsgi-horizon"],
            [
                "python3",
                "/usr/share/openstack-dashboard/manage.py",
                "migrate",
                "--noinput",
            ],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["horizon"])
        self.check_file(
            "horizon",
            "/etc/apache2/sites-available/wsgi-horizon.conf",
        )
        self.check_file(
            "horizon", "/etc/openstack-dashboard/local_settings.py"
        )

    def test_get_dashboard_url_action(self):
        """Test admin account action."""
        action_event = MagicMock()
        self.harness.charm._get_dashboard_url_action(action_event)
        action_event.set_results.assert_called_with(
            {"url": "http://10.0.0.10:80"}
        )
