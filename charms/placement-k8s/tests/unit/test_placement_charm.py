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

"""Tests for placement charm."""

import textwrap

import charm
import ops_sunbeam.test_utils as test_utils


class _PlacementOperatorCharm(charm.PlacementOperatorCharm):
    """Placement test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        """Log events."""
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        """Log configure charm call."""
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self):
        """Ingress address for charm."""
        return "placement.juju"


class TestPlacementOperatorCharm(test_utils.CharmTestCase):
    """Classes for testing placement charms."""

    PATCHES = []
    maxDiff = None

    def setUp(self):
        """Setup Placement tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _PlacementOperatorCharm, container_calls=self.container_calls
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
        """Test Pebble ready event is captured."""
        self.assertEqual(self.harness.charm.seen_events, [])
        test_utils.set_all_pebbles_ready(self.harness)
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_all_relations(self):
        """Test all the charms relations."""
        self.harness.set_leader()
        test_utils.set_all_pebbles_ready(self.harness)
        test_utils.add_all_relations(self.harness)
        id_svc_rel = self.harness.model.get_relation("identity-service")
        self.harness.update_relation_data(
            id_svc_rel.id, "keystone", {"service-domain-id": "svcdomid"}
        )
        test_utils.add_complete_ingress_relation(self.harness)

        setup_cmds = [
            ["a2ensite", "wsgi-placement-api"],
            ["sudo", "-u", "placement", "placement-manage", "db", "sync"],
        ]
        for cmd in setup_cmds:
            self.assertIn(cmd, self.container_calls.execute["placement-api"])
        self.check_file(
            "placement-api",
            "/etc/apache2/sites-available/wsgi-placement-api.conf",
        )
        expect_entries = """
        [DEFAULT]
        debug = False

        [api]
        auth_strategy = keystone

        [placement_database]
        connection = mysql+pymysql://foo:hardpassword@10.0.0.10/placement_api

        connection_recycle_time = 20
        max_pool_size = 2

        [keystone_authtoken]
        auth_url = http://keystone.internal:5000
        interface = internal
        www_authenticate_uri = http://keystone.internal:5000
        auth_type = password
        project_domain_name = svc-domain
        user_domain_name = svc-domain
        project_name = svc-project
        username = svcuser1
        password = svcpass1
        service_token_roles = None
        service_token_roles_required = True
        region_name = region12


        [service_user]
        auth_url = http://keystone.internal:5000
        send_service_user_token = true
        auth_type = password
        project_domain_id = svcdomid
        user_domain_id = svcdomid
        project_name = svc-project
        username = svcuser1
        password = svcpass1
        region_name = region12


        [placement]
        randomize_allocation_candidates = true
        """
        expect_string = textwrap.dedent(expect_entries).lstrip()
        self.check_file(
            "placement-api",
            "/etc/placement/placement.conf",
            contents=expect_string,
        )
