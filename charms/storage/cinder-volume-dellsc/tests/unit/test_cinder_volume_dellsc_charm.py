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

"""Unit tests for Cinder Dell SC operator charm class."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils
from ops_sunbeam import guard as sunbeam_guard


class _CinderVolumeDellSCOperatorCharm(charm.CinderVolumeDellSCOperatorCharm):
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


class TestCinderDellSCOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeDellSCOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeDellSCOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeDellSCOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_backend_config_maps_dedicated_secrets(self):
        """Ensure dedicated secrets are expanded into backend config."""
        self.harness.begin_with_initial_hooks()
        san_login = self.harness.add_user_secret({"san-login": "admin"})
        san_password = self.harness.add_user_secret({"san-password": "secret"})
        secondary_login = self.harness.add_user_secret(
            {"secondary-san-login": "admin2"}
        )
        secondary_password = self.harness.add_user_secret(
            {"secondary-san-password": "secret2"}
        )
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(san_login, self.harness.charm.app)
        self.harness.grant_secret(san_password, self.harness.charm.app)
        self.harness.grant_secret(secondary_login, self.harness.charm.app)
        self.harness.grant_secret(secondary_password, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "dell-sc-ssn": 64702,
                "protocol": "fc",
                "san-login": san_login,
                "san-password": san_password,
                "secondary-san-ip": "10.20.20.4",
                "secondary-san-login": secondary_login,
                "secondary-san-password": secondary_password,
            }
        )

        backend_config = self.harness.charm.get_backend_configuration()
        assert backend_config["san-login"] == "admin"
        assert backend_config["san-password"] == "secret"
        assert backend_config["secondary-san-login"] == "admin2"
        assert backend_config["secondary-san-password"] == "secret2"

    def test_backend_config_maps_shared_secret(self):
        """Ensure shared DellSC secret is expanded into backend config."""
        self.harness.begin_with_initial_hooks()
        combined_secret = self.harness.add_user_secret(
            {
                "san-login": "admin",
                "san-password": "secret",
                "secondary-san-login": "admin2",
                "secondary-san-password": "secret2",
            }
        )
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(combined_secret, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "dell-sc-ssn": 64702,
                "protocol": "fc",
                "san-login": combined_secret,
                "san-password": combined_secret,
                "secondary-san-ip": "10.20.20.4",
                "secondary-san-login": combined_secret,
                "secondary-san-password": combined_secret,
            }
        )

        backend_config = self.harness.charm.get_backend_configuration()
        assert backend_config["san-login"] == "admin"
        assert backend_config["san-password"] == "secret"
        assert backend_config["secondary-san-login"] == "admin2"
        assert backend_config["secondary-san-password"] == "secret2"

    def test_backend_config_requires_dell_sc_ssn(self):
        """Ensure dell-sc-ssn is required."""
        self.harness.begin_with_initial_hooks()
        san_login = self.harness.add_user_secret({"san-login": "admin"})
        san_password = self.harness.add_user_secret({"san-password": "secret"})
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(san_login, self.harness.charm.app)
        self.harness.grant_secret(san_password, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "san-login": san_login,
                "san-password": san_password,
                "protocol": "fc",
            }
        )

        with self.assertRaises(sunbeam_guard.WaitingExceptionError):
            self.harness.charm.get_backend_configuration()
