#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
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

"""Unit tests for Neutron Baremetal Switch Config K8S Operator."""

import unittest.mock as mock

import charm
import charms.neutron_k8s.v0.switch_config as switch_config
import ops_sunbeam.test_utils as test_utils
from ops import (
    model,
)


_SAMPLE_CONFIG = """driver = "netconf-openconfig"
device_params = "name:nexus"
switch_info = "nexus"
switch_id = "00:53:00:0a:0a:0a"
host = "nexus.example.net"
username = "user"
"""

_NEXUS_SAMPLE_CONFIG = '["nexus.example.net"]\n' + _SAMPLE_CONFIG
_KEY_LINE = 'key_filename = "/etc/neutron/ssh_keys/nexus_sshkey"'


class _NeutronBaremetalSwitchConfigCharm(charm.NeutronBaremetalSwitchConfigCharm):
    pass


class TestNeutronBaremetalSwitchConfigCharm(test_utils.CharmTestCase):
    """Unit tests for Neutron Baremetal Switch Config K8S Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(_NeutronBaremetalSwitchConfigCharm)

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader()

    def add_switch_config_relation(self) -> None:
        """Add the switch-config relation."""
        return self.harness.add_relation(
            charm.SWITCH_CONFIG_RELATION_NAME,
            "neutron",
        )

    @mock.patch("ops.model.Secret.grant")
    def test_all_relations(self, mock_grant):
        """Test all integrations for operator."""
        self.add_switch_config_relation()

        secret_data = {
            "conf": "\n".join([_NEXUS_SAMPLE_CONFIG, _KEY_LINE]),
            "nexus-sshkey": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        self.harness.update_config({"conf-secrets": secret})

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.ActiveStatus)

        rel = self.harness.model.get_relation("switch-config")
        mock_grant.assert_called_once_with(rel)
        rel_data = rel.data[self.harness.model.app]
        expected_data = {
            switch_config.SWITCH_CONFIG: secret,
        }
        self.assertEqual(expected_data, rel_data)

    def test_invalid_secrets(self):
        """Test case involving the conf-secrets."""
        # conf-secrets config option must be set.
        config = {"conf-secrets": ""}
        self.harness.update_config(config)

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)

        # secret does not exist.
        config = {"conf-secrets": "foo"}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # invalid owner.
        secret = self.harness.add_model_secret("foo", {"conf": _NEXUS_SAMPLE_CONFIG})

        self.harness.update_config({"conf-secrets": secret})

        self.assertIsInstance(unit.status, model.BlockedStatus)

    def test_validate_configs(self):
        """Test case involving switch config validation."""
        # "conf" key is expected.
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"foo": "lish"},
        )

        self.harness.update_config({"conf-secrets": secret})

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)

        # malformed config.
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": "lish"},
        )

        self.harness.update_config({"conf-secrets": secret})

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # unknown field in config section.
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": "\n".join([_NEXUS_SAMPLE_CONFIG, "foo = 10"])},
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # valid config.
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": _NEXUS_SAMPLE_CONFIG},
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.ActiveStatus)

    def test_duplicate_sections(self):
        """Test case for duplicate switch configurations."""
        secret_1 = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": _NEXUS_SAMPLE_CONFIG},
        )
        secret_2 = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": _NEXUS_SAMPLE_CONFIG},
        )

        config = {"conf-secrets": ",".join([secret_1, secret_2])}
        self.harness.update_config(config)

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)

        # a secret can contain multiple sections.
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": "\n".join([_NEXUS_SAMPLE_CONFIG] * 2)},
        )

        self.harness.update_config({"conf-secrets": secret})

        self.assertIsInstance(unit.status, model.BlockedStatus)

    def test_missing_additional_file(self):
        """Test case for missing additional files."""
        secret_data = {
            "conf": "\n".join([_NEXUS_SAMPLE_CONFIG, _KEY_LINE]),
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        self.harness.update_config({"conf-secrets": secret})

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)
