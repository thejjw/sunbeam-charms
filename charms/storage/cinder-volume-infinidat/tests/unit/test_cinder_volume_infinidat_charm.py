#!/usr/bin/env python3

# Copyright 2026 Canonical Ltd.
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

"""Unit tests for Cinder Infinidat operator charm."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils
import pydantic
import pytest


class _CinderVolumeInfinidatOperatorCharm(charm.CinderVolumeInfinidatOperatorCharm):
    """Charm wrapper for test usage."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)
        self._snap = Mock()

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def get_snap(self):
        return self._snap


def add_complete_cinder_volume_relation(harness: ops.testing.Harness) -> int:
    """Add a complete cinder-volume relation to the charm."""
    return harness.add_relation(
        "cinder-volume",
        "cinder-volume",
        unit_data={
            "snap-name": "cinder-volume",
        },
    )


class TestCinderInfinidatOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeInfinidatOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeInfinidatOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeInfinidatOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        secret_login = self.harness.add_user_secret({"san-login": "test-login"})
        secret_password = self.harness.add_user_secret(
            {"san-password": "test-password"}
        )
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(secret_login, self.harness.charm.app)
        self.harness.grant_secret(secret_password, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "san-login": secret_login,
                "san-password": secret_password,
                "infinidat-pool-name": "pool1",
                "protocol": "iscsi",
                "infinidat-iscsi-netspaces": "netspace1",
            }
        )
        self.harness.evaluate_status()
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )


class TestInfinidatConfigValidation(test_utils.CharmTestCase):
    """Test cases for Infinidat configuration validation."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeInfinidatOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeInfinidatOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin_with_initial_hooks()

    def _get_config_class(self):
        """Return the configuration class from the charm."""
        return self.harness.charm.configuration_class

    def _mock_secret(self, data):
        """Create a mock ops.Secret that returns the given data."""
        secret = Mock()
        secret.get_content.return_value = data
        return secret

    def test_iscsi_requires_netspaces(self):
        """Validation fails when protocol is iscsi without netspaces."""
        config_class = self._get_config_class()
        with pytest.raises(
            pydantic.ValidationError, match="infinidat-iscsi-netspaces"
        ):
            config_class(
                san_ip="10.20.20.3",
                san_login=self._mock_secret({"san-login": "admin"}),
                san_password=self._mock_secret({"san-password": "secret123"}),
                infinidat_pool_name="pool1",
                protocol="iscsi",
            )

    def test_iscsi_with_netspaces_passes(self):
        """Validation passes when protocol is iscsi with netspaces set."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="iscsi",
            infinidat_iscsi_netspaces="netspace1",
        )
        self.assertEqual(config.protocol, "iscsi")
        self.assertEqual(config.infinidat_iscsi_netspaces, "netspace1")

    def test_fc_without_netspaces_passes(self):
        """Validation passes when protocol is fc without netspaces."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="fc",
        )
        self.assertEqual(config.protocol, "fc")
        self.assertIsNone(config.infinidat_iscsi_netspaces)

    def test_protocol_defaults_to_iscsi(self):
        """Protocol should default to iscsi from charm config metadata."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            infinidat_iscsi_netspaces="netspace1",
        )
        self.assertEqual(config.protocol, "iscsi")

    def test_use_chap_auth_defaults_true(self):
        """CHAP auth should default to true from charm config metadata."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="fc",
        )
        self.assertTrue(config.use_chap_auth)

    def test_driver_use_ssl_defaults_false(self):
        """HTTPS should be opt-in for the Infinidat management API."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="fc",
        )
        self.assertFalse(config.driver_use_ssl)

    def test_driver_use_ssl_accepts_true(self):
        """HTTPS can be enabled for InfiniBox API connectivity."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="fc",
            driver_use_ssl=True,
        )
        self.assertTrue(config.driver_use_ssl)

    def test_chap_username_requires_password(self):
        """Setting a CHAP username without a password fails validation."""
        config_class = self._get_config_class()
        with pytest.raises(pydantic.ValidationError, match="chap_password"):
            config_class(
                san_ip="10.20.20.3",
                san_login=self._mock_secret({"san-login": "admin"}),
                san_password=self._mock_secret({"san-password": "secret123"}),
                infinidat_pool_name="pool1",
                protocol="fc",
                chap_username=self._mock_secret(
                    {"chap-username": "chap-user"}
                ),
            )

    def test_chap_password_requires_username(self):
        """Setting a CHAP password without a username fails validation."""
        config_class = self._get_config_class()
        with pytest.raises(pydantic.ValidationError, match="chap_username"):
            config_class(
                san_ip="10.20.20.3",
                san_login=self._mock_secret({"san-login": "admin"}),
                san_password=self._mock_secret({"san-password": "secret123"}),
                infinidat_pool_name="pool1",
                protocol="fc",
                chap_password=self._mock_secret(
                    {"chap-password": "chap-pass"}
                ),
            )

    def test_chap_credentials_pair_passes(self):
        """Supplying both CHAP credentials passes validation."""
        config_class = self._get_config_class()
        config = config_class(
            san_ip="10.20.20.3",
            san_login=self._mock_secret({"san-login": "admin"}),
            san_password=self._mock_secret({"san-password": "secret123"}),
            infinidat_pool_name="pool1",
            protocol="fc",
            chap_username=self._mock_secret({"chap-username": "chap-user"}),
            chap_password=self._mock_secret({"chap-password": "chap-pass"}),
        )
        self.assertEqual(config.chap_username, "chap-user")
        self.assertEqual(config.chap_password, "chap-pass")
