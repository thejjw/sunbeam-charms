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

"""Unit tests for Cinder datacore operator charm."""

from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils
import pydantic


class _CinderVolumeDatacoreOperatorCharm(
    charm.CinderVolumeDatacoreOperatorCharm
):
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


class TestCinderDatacoreOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeDatacoreOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeDatacoreOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeDatacoreOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        add_complete_cinder_volume_relation(self.harness)
        secret = self.harness.add_user_secret(
            {"san-login": "admin", "san-password": "s3cr3t"}
        )
        self.harness.grant_secret(secret, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "san-login": secret,
                "san-password": secret,
            }
        )
        self.harness.evaluate_status()
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )

    def test_credential_config_types(self):
        """Login and password are both typed as Juju secrets."""
        self.harness.begin()
        config = self.harness.charm.meta.config

        self.assertEqual(config["san-login"].type, "secret")
        self.assertEqual(config["san-password"].type, "secret")

    def test_credentials_resolved_from_shared_secret(self):
        """Login and password are resolved from one shared secret.

        Sunbeam stores every secret field of a backend in a single
        Juju secret and points each charm option at the same URI, so
        each option must read its own key out of that secret.
        """
        self.harness.begin()
        shared_secret = self.harness.add_user_secret(
            {"san-login": "admin", "san-password": "s3cr3t"}
        )
        self.harness.grant_secret(shared_secret, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "protocol": "iscsi",
                "san-login": shared_secret,
                "san-password": shared_secret,
            }
        )

        backend = self.harness.charm.get_backend_configuration()

        self.assertEqual(backend["san-login"], "admin")
        self.assertEqual(backend["san-password"], "s3cr3t")
        for option in ("san-login", "san-password"):
            with self.subTest(option=option):
                self.assertFalse(str(backend[option]).startswith("secret:"))

    def test_rejects_login_secret_without_login_key(self):
        """A login secret missing the san-login key is rejected."""
        self.harness.begin()
        secret = Mock()
        secret.get_content.return_value = {"san-password": "s3cr3t"}
        login_type = self.harness.charm._configuration_type_overrides()[
            "san-login"
        ]

        with self.assertRaises(ValueError):
            pydantic.TypeAdapter(login_type).validate_python(secret)
