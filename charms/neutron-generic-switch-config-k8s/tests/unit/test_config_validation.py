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

"""Plain pytest unit tests for config validation in neutron-generic-switch-config-k8s."""

from unittest.mock import (
    MagicMock,
)

import charm
import ops_sunbeam.guard as sunbeam_guard
import pytest
from ops import (
    model,
)

_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = %(device_type)s
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
"""


def _get_sample_config(
    name: str, device_type: str, with_key: bool = True
) -> str:
    config = _SAMPLE_CONFIG % {"name": name, "device_type": device_type}
    if with_key:
        config = config + "\nkey_file = /etc/neutron/sshkeys/%s-key" % name
    return config


ARISTA_CONFIG = _get_sample_config("arista", "netmiko_arista_eos")
ARISTA_CONFIG_NO_KEY = _get_sample_config(
    "arista", "netmiko_arista_eos", with_key=False
)


class _Validator:
    """Thin wrapper exposing the charm's validation methods under test."""

    _get_secrets = charm.NeutronGenericSwitchConfigCharm._get_secrets
    _validate_configs = charm.NeutronGenericSwitchConfigCharm._validate_configs
    _validate_section = charm.NeutronGenericSwitchConfigCharm._validate_section


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

    def test_section_not_genericswitch(self, validator):
        """Section name not starting with 'genericswitch:' → blocked."""
        conf = ARISTA_CONFIG.replace("genericswitch:arista", "arista")
        secret = _make_secret({"conf": conf, "arista-key": "foo"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_missing_device_type(self, validator):
        """Missing device_type field → blocked."""
        conf = ARISTA_CONFIG.replace("netmiko_arista_eos", "")
        secret = _make_secret({"conf": conf, "arista-key": "foo"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_unknown_field(self, validator):
        """Unknown config field → blocked."""
        secret = _make_secret(
            {
                "conf": "\n".join([ARISTA_CONFIG, "foo = 10"]),
                "arista-key": "foo",
            }
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_invalid_key_file_path(self, validator):
        """key_file with wrong base path → blocked."""
        conf = "\n".join(
            [ARISTA_CONFIG_NO_KEY, 'key_file = "/foo/arista-key"']
        )
        secret = _make_secret({"conf": conf, "arista-key": "foo"})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])

    def test_valid_config(self, validator):
        """Valid config → no exception."""
        secret = _make_secret(
            {
                "conf": ARISTA_CONFIG,
                "arista-key": "foo",
            }
        )
        validator._validate_configs([secret])


class TestDuplicateSections:
    """Tests for duplicate section detection."""

    def test_duplicate_across_secrets(self, validator):
        """Same section in two different secrets → blocked."""
        secret_1 = _make_secret(
            {"conf": ARISTA_CONFIG, "arista-key": "foo"},
            secret_id="secret:1",
        )
        secret_2 = _make_secret(
            {"conf": ARISTA_CONFIG, "arista-key": "foo"},
            secret_id="secret:2",
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret_1, secret_2])

    def test_duplicate_within_secret(self, validator):
        """Same section repeated within one secret → blocked."""
        secret = _make_secret(
            {
                "conf": "\n".join([ARISTA_CONFIG] * 2),
                "arista-key": "foo",
            }
        )
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])


class TestMissingAdditionalFile:
    """Tests for missing additional files."""

    def test_missing_key_file(self, validator):
        """key_file references file not in secret → blocked."""
        secret = _make_secret({"conf": ARISTA_CONFIG})
        with pytest.raises(sunbeam_guard.BlockedExceptionError):
            validator._validate_configs([secret])
