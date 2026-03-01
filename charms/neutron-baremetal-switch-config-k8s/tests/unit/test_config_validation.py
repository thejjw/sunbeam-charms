#!/usr/bin/env python3

# Copyright 2025 Canonical Ltd.
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

"""Plain pytest unit tests for config validation in neutron-baremetal-switch-config-k8s."""

from unittest.mock import (
    MagicMock,
)

import charm
import ops_sunbeam.guard as sunbeam_guard
import pytest
from ops import (
    model,
)

_SAMPLE_CONFIG = """driver = netconf-openconfig
device_params = name:nexus
switch_info = nexus
switch_id = 00:53:00:0a:0a:0a
host = nexus.example.net
username = user
"""

NEXUS_SAMPLE_CONFIG = "[nexus.example.net]\n" + _SAMPLE_CONFIG
KEY_LINE = "key_filename = /etc/neutron/sshkeys/nexus-sshkey"


class _Validator:
    """Thin wrapper exposing the charm's validation methods under test."""

    _get_secrets = charm.NeutronBaremetalSwitchConfigCharm._get_secrets
    _validate_configs = (
        charm.NeutronBaremetalSwitchConfigCharm._validate_configs
    )
    _validate_section = (
        charm.NeutronBaremetalSwitchConfigCharm._validate_section
    )


def _make_secret(content, secret_id="secret:test"):
    """Create a mock secret with the given content."""
    secret = MagicMock()
    secret.id = secret_id
    secret.get_content.return_value = content
    return secret


@pytest.fixture()
def validator():
    """Return a _Validator instance."""
    return _Validator()


class TestInvalidSecrets:
    """Tests for _get_secrets with invalid configurations."""

    def test_empty_conf_secrets(self, validator):
        """Empty conf-secrets config option → blocked."""
        validator.config = {"conf-secrets": ""}
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._get_secrets()

    def test_secret_not_found(self, validator):
        """Non-existent secret ID → blocked."""
        validator.config = {"conf-secrets": "secret:nonexistent"}
        validator.model = MagicMock()
        validator.model.get_secret.side_effect = model.SecretNotFoundError
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._get_secrets()


class TestValidateConfigs:
    """Tests for _validate_configs with various config payloads."""

    def test_missing_conf_key(self, validator):
        """Secret without 'conf' key → blocked."""
        secret = _make_secret({"foo": "lish"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_malformed_config(self, validator):
        """Malformed INI content → blocked."""
        secret = _make_secret({"conf": "lish"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_unknown_field(self, validator):
        """Unknown config field → blocked."""
        secret = _make_secret(
            {"conf": "\n".join([NEXUS_SAMPLE_CONFIG, "foo = 10"])}
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_invalid_key_filename_path(self, validator):
        """key_filename with wrong base path → blocked."""
        conf = "\n".join(
            [NEXUS_SAMPLE_CONFIG, 'key_filename = "/foo/nexus-sshkey"']
        )
        secret = _make_secret({"conf": conf, "nexus-sshkey": "foo"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_valid_config(self, validator):
        """Valid config with no key → no exception."""
        secret = _make_secret({"conf": NEXUS_SAMPLE_CONFIG})
        validator._validate_configs([secret])

    def test_valid_config_with_key(self, validator):
        """Valid config with SSH key → no exception."""
        secret = _make_secret(
            {
                "conf": "\n".join([NEXUS_SAMPLE_CONFIG, KEY_LINE]),
                "nexus-sshkey": "foo",
            }
        )
        validator._validate_configs([secret])


class TestDuplicateSections:
    """Tests for duplicate section detection."""

    def test_duplicate_across_secrets(self, validator):
        """Same section in two different secrets → blocked."""
        secret_1 = _make_secret(
            {"conf": NEXUS_SAMPLE_CONFIG}, secret_id="secret:1"
        )
        secret_2 = _make_secret(
            {"conf": NEXUS_SAMPLE_CONFIG}, secret_id="secret:2"
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret_1, secret_2])

    def test_duplicate_within_secret(self, validator):
        """Same section repeated within one secret → blocked."""
        secret = _make_secret({"conf": "\n".join([NEXUS_SAMPLE_CONFIG] * 2)})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])


class TestMissingAdditionalFile:
    """Tests for missing additional files."""

    def test_missing_key_file(self, validator):
        """key_filename references file not in secret → blocked."""
        secret = _make_secret(
            {
                "conf": "\n".join([NEXUS_SAMPLE_CONFIG, KEY_LINE]),
            }
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])
