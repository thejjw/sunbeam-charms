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

"""Unit tests for Heat operator."""

import json
from unittest.mock import (
    MagicMock,
    Mock,
)

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    Harness,
)


class _HeatTestOperatorCharm(charm.HeatOperatorCharm):
    """Test Operator Charm for Heat Operator."""

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
        return "heat.juju"


class TestHeatOperatorCharm(test_utils.CharmTestCase):
    """Unit tests for Heat Operator."""

    PATCHES = []

    def setUp(self):
        """Run setup for unit tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _HeatTestOperatorCharm, container_calls=self.container_calls
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

    def add_complete_identity_resource_relation(self, harness: Harness) -> int:
        """Add complete Identity resource relation."""
        rel_id = harness.add_relation("identity-ops", "keystone")
        harness.add_relation_unit(rel_id, "keystone/0")
        harness.charm.user_id_ops.get_config_credentials = Mock(
            return_value=("test", "test")
        )

        harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "response": json.dumps(
                    {
                        "id": 1,
                        "tag": "initial_heat_domain_setup",
                        "ops": [{"name": "create_domain", "return-code": 0}],
                    }
                )
            },
        )
        return rel_id

    def add_complete_ingress_relation(self, harness: Harness) -> None:
        """Add complete traefik-route relations."""
        harness.add_relation(
            "traefik-route-public",
            "heat",
            app_data={"external_host": "dummy-ip", "scheme": "http"},
        )
        harness.add_relation(
            "traefik-route-internal",
            "heat",
            app_data={"external_host": "dummy-ip", "scheme": "http"},
        )

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        secret_mock = MagicMock()
        secret_mock.id = "test-secret-id"
        secret_mock.get_content.return_value = {
            "username": "fake-username",
            "password": "fake-password",
        }
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(len(self.harness.charm.seen_events), 3)

    def test_all_relations(self):
        """Test all integrations for operator."""
        secret_mock = MagicMock()
        secret_mock.id = "test-secret-id"
        secret_mock.get_content.return_value = {
            "username": "fake-username",
            "password": "fake-password",
        }
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        # this adds all the default/common relations
        test_utils.add_all_relations(self.harness)
        self.add_complete_identity_resource_relation(self.harness)

        # add the traefik-route-internal relation last.
        self.add_complete_ingress_relation(self.harness)

        setup_cmds = [["heat-manage", "db_sync"]]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["heat-api"])
        config_files = ["/etc/heat/heat.conf", "/etc/heat/api-paste.ini"]
        for f in config_files:
            self.check_file("heat-api", f)
        config_files = [
            "/etc/heat/heat-api-cfn.conf",
            "/etc/heat/api-paste-cfn.ini",
        ]
        for f in config_files:
            self.check_file("heat-api-cfn", f)
