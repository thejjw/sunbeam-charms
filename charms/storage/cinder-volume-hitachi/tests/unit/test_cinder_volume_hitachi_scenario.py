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

"""ops.testing (state-transition) tests for cinder-volume-hitachi.

This charm is a subordinate charm.  The mandatory relation is
cinder-volume (requires, container scope).
"""

import datetime

import charm
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
from ops import (
    testing,
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


def test_mirror_certificate_uses_consumer_content_key():
    """Forward mirror certificate content under the Cinder consumer key."""
    with pytest.warns(
        PendingDeprecationWarning, match="Harness is deprecated"
    ):
        harness = testing.Harness(charm.CinderVolumeHitachiOperatorCharm)
    try:
        harness.begin()
        credential = harness.add_user_secret(
            {"san-login": "admin", "san-password": "secret"}
        )
        harness.grant_secret(credential, harness.charm.app)
        driver_cert = certificate("hitachi-primary")
        mirror_cert = certificate("hitachi-mirror")
        harness.update_config(
            {
                "san-ip": "10.0.0.1",
                "san-login": credential,
                "san-password": credential,
                "hitachi-storage-id": "450000",
                "hitachi-pools": "pool0",
                "driver-ssl-cert": driver_cert,
                "hitachi-mirror-ssl-cert": mirror_cert,
            }
        )

        backend = harness.charm.get_backend_configuration()

        assert backend["driver-ssl-cert"] == driver_cert
        assert backend["hitachi-mirror-ssl-cert"] == mirror_cert
        assert "hitachi-mirror-driver-ssl-cert" not in backend
    finally:
        harness.cleanup()


class TestAllRelations:
    """Config-changed with all mandatory relations present."""

    def test_all_relations(self, ctx, complete_state):
        """With all mandatory relations, charm should proceed past relation checks."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite all "
                f"mandatory relations present: {status.message}"
            )
