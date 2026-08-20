# Copyright 2026 Canonical Ltd.
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

"""Tests for shared storage configuration validation."""

import datetime

import ops_sunbeam.storage as storage
import pytest
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


def _certificate(common_name: str) -> str:
    """Create a valid self-signed PEM certificate."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def _private_key() -> str:
    """Create an unencrypted PEM private key."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def test_certificate_bundle_validator_accepts_multiple_certificates():
    """Certificate bundles may contain more than one PEM certificate."""
    bundle = _certificate("first") + _certificate("second")

    assert storage.certificate_bundle_validator(bundle) == bundle


def test_certificate_bundle_validator_rejects_non_pem_text():
    """A filesystem path is not certificate bundle content."""
    with pytest.raises(ValueError, match="Invalid certificate format"):
        storage.certificate_bundle_validator("/tmp/ca.pem")


def test_private_key_validator_accepts_pem_private_key():
    """An unencrypted PEM private key is accepted."""
    private_key = _private_key()

    assert storage.private_key_validator(private_key) == private_key


def test_private_key_validator_rejects_non_pem_text():
    """A filesystem path is not private-key content."""
    with pytest.raises(ValueError, match="Invalid private key format"):
        storage.private_key_validator("/tmp/client.key")
