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

"""Define keystone tests."""

import base64
import json

import ops_sunbeam.test_utils as test_utils
from ops.testing import Harness

import charm


class _KeystoneLDAPK8SCharm(charm.KeystoneLDAPK8SCharm):
    """Create Keystone operator test charm."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self) -> str:
        return "10.0.0.10"


class TestKeystoneLDAPK8SCharm(test_utils.CharmTestCase):
    def setUp(self):
        """Run test setup."""
        self.harness = Harness(charm.KeystoneLDAPK8SCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_charm(self):
        """Test pebble ready handler."""
        self.harness.set_leader()
        rel_id = self.harness.add_relation("domain-config", "keystone")
        self.harness.add_relation_unit(rel_id, "keystone/0")
        rel_data = self.harness.get_relation_data(rel_id, self.harness.charm.unit.app.name)
        ldap_config_flags = json.dumps(
            {
                "group_tree_dn": "ou=groups,dc=test,dc=com",
                "group_objectclass": "posixGroup",
                "group_name_attribute": "cn",
                "group_member_attribute": "memberUid",
                "group_members_are_ids": "true",
            }
        )
        self.harness.update_config(
            {
                "ldap-server": "ldap://10.1.176.184",
                "ldap-user": "cn=admin,dc=test,dc=com",
                "ldap-password": "crapper",
                "ldap-suffix": "dc=test,dc=com",
                "domain-name": "userdomain",
                "ldap-config-flags": ldap_config_flags,
            }
        )
        self.assertEqual("userdomain", rel_data["domain-name"])
        contents = base64.b64decode(rel_data["config-contents"]).decode()
        self.assertIn("password = crapper", contents)
        self.assertIn("group_objectclass = posixGroup", contents)
