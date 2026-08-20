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

"""Unit tests for Cinder NetApp operator charm."""

import datetime
from unittest.mock import (
    MagicMock,
    Mock,
    patch,
)

import charm
import ops.testing
import ops_sunbeam.test_utils as test_utils
import pydantic
from cryptography import (
    x509,
)
from cryptography.hazmat.primitives import (
    hashes,
    serialization,
)
from cryptography.hazmat.primitives.asymmetric import (
    rsa,
)
from cryptography.x509.oid import (
    NameOID,
)


class _CinderVolumeNetAppOperatorCharm(charm.CinderVolumeNetAppOperatorCharm):
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


def certificate(common_name: str) -> str:
    """Create a valid self-signed PEM certificate."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    value = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return value.public_bytes(serialization.Encoding.PEM).decode()


def private_key() -> str:
    """Create an unencrypted PEM private key."""
    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class TestCinderNetAppOperatorCharm(test_utils.CharmTestCase):
    """Test cases for CinderVolumeNetAppOperatorCharm class."""

    PATCHES = []

    def setUp(self):
        """Setup fixtures ready for testing."""
        super().setUp(charm, self.PATCHES)
        self.mock_event = MagicMock()
        self.snap = Mock()
        snap_patch = patch.object(
            _CinderVolumeNetAppOperatorCharm,
            "_import_snap",
            Mock(return_value=self.snap),
        )
        snap_patch.start()
        self.harness = test_utils.get_harness(
            _CinderVolumeNetAppOperatorCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(snap_patch.stop)
        self.addCleanup(self.harness.cleanup)

    def test_tls_config_types(self):
        """Only the NetApp private key uses a Juju secret."""
        self.harness.begin()
        config = self.harness.charm.meta.config

        self.assertEqual(config["netapp-ssl-cert-path"].type, "string")
        self.assertEqual(config["netapp-certificate-file"].type, "string")
        self.assertEqual(config["netapp-ca-certificate-file"].type, "string")
        self.assertEqual(config["netapp-private-key-file"].type, "secret")

    def test_tls_material_is_forwarded(self):
        """Public config and secret private key reach snap configuration."""
        self.harness.begin()
        ca_bundle = certificate("first-ca") + certificate("second-ca")
        client_certificate = certificate("client")
        ca_certificate = certificate("client-ca")
        key = private_key()
        key_secret = self.harness.add_user_secret(
            {"netapp-private-key-file": key}
        )
        self.harness.grant_secret(key_secret, self.harness.charm.app)
        self.harness.update_config(
            {
                "san-ip": "10.20.20.3",
                "protocol": "iscsi",
                "netapp-storage-protocol": "iscsi",
                "netapp-ssl-cert-path": ca_bundle,
                "netapp-private-key-file": key_secret,
                "netapp-certificate-file": client_certificate,
                "netapp-ca-certificate-file": ca_certificate,
            }
        )

        backend = self.harness.charm.get_backend_configuration()

        self.assertEqual(backend["netapp-ssl-cert-path"], ca_bundle)
        self.assertEqual(backend["netapp-private-key-file"], key)
        self.assertEqual(
            backend["netapp-certificate-file"], client_certificate
        )
        self.assertEqual(backend["netapp-ca-certificate-file"], ca_certificate)

    def test_rejects_invalid_public_tls_material(self):
        """Path strings are rejected for each public TLS input."""
        self.harness.begin()
        overrides = self.harness.charm._configuration_type_overrides()

        expected_errors = {
            "netapp-ssl-cert-path": "Invalid certificate format",
            "netapp-certificate-file": "Certificate must be PEM formatted",
            "netapp-ca-certificate-file": "Invalid certificate format",
        }
        for key, expected_error in expected_errors.items():
            with self.subTest(key=key):
                validator = pydantic.TypeAdapter(overrides[key])
                with self.assertRaisesRegex(ValueError, expected_error):
                    validator.validate_python("/tmp/netapp.pem")

    def test_rejects_invalid_private_key_secret(self):
        """Private-key secret content must contain a PEM private key."""
        self.harness.begin()
        secret = Mock()
        secret.get_content.return_value = {
            "netapp-private-key-file": "/tmp/netapp.key"
        }
        private_key_type = self.harness.charm._configuration_type_overrides()[
            "netapp-private-key-file"
        ]

        with self.assertRaisesRegex(ValueError, "Invalid private key format"):
            pydantic.TypeAdapter(private_key_type).validate_python(secret)

    def test_all_relations(self):
        """Test charm in context of full set of relations."""
        self.harness.begin_with_initial_hooks()
        # Add secret for the secret-type config field
        # Use the secret_key value from the driver spec for the secret dict
        secret = self.harness.add_user_secret(
            {"netapp-password": "test-value"}
        )
        add_complete_cinder_volume_relation(self.harness)
        self.harness.grant_secret(secret, self.harness.charm.app)
        # Update config with required fields and the secret reference
        self.harness.update_config(
            {"san-ip": "10.20.20.3", "netapp-password": secret}
        )
        self.harness.evaluate_status()
        self.assertSetEqual(
            self.harness.charm.get_mandatory_relations_not_ready(
                self.mock_event
            ),
            set(),
        )
