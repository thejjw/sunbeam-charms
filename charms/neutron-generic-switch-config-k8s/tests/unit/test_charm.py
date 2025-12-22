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

"""Unit tests for Neutron Generic Switch Config K8S Operator."""

import charm
import charms.neutron_k8s.v0.switch_config as switch_config
import ops_sunbeam.test_utils as test_utils
from ops import (
    model,
)

_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = %(device_type)s
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
"""


def _get_sample_config(name: str, device_type: str, with_key=True) -> str:
    config = _SAMPLE_CONFIG % {"name": name, "device_type": device_type}
    if with_key:
        config = config + '\nkey_file = /etc/neutron/sshkeys/%s-key' % name

    return config


class _NeutronGenericSwitchConfigCharm(charm.NeutronGenericSwitchConfigCharm):
    pass


class TestNeutronGenericSwitchConfigCharm(test_utils.CharmTestCase):
    """Unit tests for Neutron Generic Switch Config K8S Operator."""

    PATCHES = []

    def setUp(self):
        """Setup test fixtures for test."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(_NeutronGenericSwitchConfigCharm)

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.harness.set_leader()

    def add_switch_config_relation(self) -> None:
        """Add the switch-config relation."""
        return self.harness.add_relation(
            charm.SWITCH_CONFIG_RELATION_NAME,
            "neutron",
        )

    def test_all_relations(self):
        """Test all integrations for operator."""
        self.add_switch_config_relation()

        secret_data = {
            "conf": _get_sample_config("arista", "netmiko_arista_eos"),
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        self.harness.update_config({"conf-secrets": secret})

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.ActiveStatus)

        rel = self.harness.model.get_relation("switch-config")
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
        secret_data = {
            "conf": _get_sample_config("arista", "netmiko_arista_eos"),
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            "foo",
            secret_data,
        )

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
        conf = _get_sample_config("arista", "netmiko_arista_eos")
        secret_data = {
            "conf": "\n".join([conf, "foo = 10"]),
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # missing device_type.
        bad_conf = conf.replace("netmiko_arista_eos", "")
        secret_data = {
            "conf": bad_conf,
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # invalid key_file.
        data = "\n".join(
            [
                _get_sample_config("arista", "netmiko_arista_eos", False),
                'key_file = "/foo/arista-key"',
            ]
        )
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            {"conf": data, "arista-key": "foo"},
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.BlockedStatus)

        # valid config.
        secret_data = {
            "conf": conf,
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        config = {"conf-secrets": secret}
        self.harness.update_config(config)

        self.assertIsInstance(unit.status, model.ActiveStatus)

    def test_duplicate_sections(self):
        """Test case for duplicate switch configurations."""
        conf = _get_sample_config("arista", "netmiko_arista_eos")
        secret_data = {
            "conf": "\n".join([conf, "foo = 10"]),
            "arista-key": "foo",
        }
        secret_1 = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )
        secret_2 = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        config = {"conf-secrets": ",".join([secret_1, secret_2])}
        self.harness.update_config(config)

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)

        # a secret can contain multiple sections.
        secret_data = {
            "conf": "\n".join([conf] * 2),
            "arista-key": "foo",
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        self.harness.update_config({"conf-secrets": secret})

        self.assertIsInstance(unit.status, model.BlockedStatus)

    def test_missing_additional_file(self):
        """Test case for missing additional files."""
        secret_data = {
            "conf": _get_sample_config("arista", "netmiko_arista_eos"),
        }
        secret = self.harness.add_model_secret(
            self.harness.charm.app.name,
            secret_data,
        )

        self.harness.update_config({"conf-secrets": secret})

        unit = self.harness.charm.unit
        self.assertIsInstance(unit.status, model.BlockedStatus)
