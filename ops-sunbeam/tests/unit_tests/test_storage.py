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
import types

import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.storage as storage
import pydantic
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


def test_certificate_validator_accepts_one_certificate():
    """The single-certificate validator accepts one PEM certificate."""
    certificate = _certificate("single")

    assert storage.certificate_validator(certificate) == certificate


def test_certificate_validator_rejects_multiple_certificates():
    """The single-certificate validator rejects certificate bundles."""
    bundle = _certificate("first") + _certificate("second")

    with pytest.raises(ValueError, match="Invalid certificate format"):
        storage.certificate_validator(bundle)


def test_certificate_bundle_validator_rejects_non_pem_text():
    """A filesystem path is not certificate bundle content."""
    with pytest.raises(ValueError, match="Invalid certificate format"):
        storage.certificate_bundle_validator("/tmp/ca.pem")


@pytest.mark.parametrize(
    "validator",
    [storage.certificate_validator, storage.certificate_bundle_validator],
)
@pytest.mark.parametrize(
    "payload_order",
    [
        "certificate-key",
        "key-certificate",
        "junk-certificate",
        "certificate-junk",
    ],
)
def test_public_certificate_validators_reject_non_certificate_content(
    validator, payload_order
):
    """Ordinary public config cannot carry private keys or other content."""
    certificate = _certificate("public")
    private_key = _private_key()
    parts = {
        "certificate": certificate,
        "key": private_key,
        "junk": "not PEM material\n",
    }
    payload = "".join(parts[name] for name in payload_order.split("-"))

    with pytest.raises(ValueError, match="Invalid certificate format"):
        validator(payload)


def test_private_key_validator_accepts_pem_private_key():
    """An unencrypted PEM private key is accepted."""
    private_key = _private_key()

    assert storage.private_key_validator(private_key) == private_key


def test_private_key_validator_rejects_non_pem_text():
    """A filesystem path is not private-key content."""
    with pytest.raises(ValueError, match="Invalid private key format"):
        storage.private_key_validator("/tmp/client.key")


@pytest.mark.parametrize(
    "payload_order",
    [
        "key-key",
        "certificate-key",
        "key-certificate",
        "junk-key",
        "key-junk",
    ],
)
def test_private_key_validator_rejects_additional_content(payload_order):
    """A private-key secret contains exactly one PEM private key."""
    private_key = _private_key()
    parts = {
        "certificate": _certificate("public"),
        "key": private_key,
        "junk": "not PEM material\n",
    }
    payload = "".join(parts[name] for name in payload_order.split("-"))

    with pytest.raises(ValueError, match="Invalid private key format"):
        storage.private_key_validator(payload)


def _driver_config_class(declare_blocksize=True) -> type[pydantic.BaseModel]:
    """Build the config model for a driver charm."""
    options = [
        ("san-ip", "string"),
        ("volume-backend-name", "string"),
        ("backend-availability-zone", "string"),
        ("protocol", "string"),
    ]
    if declare_blocksize:
        options.append(("volume-dd-blocksize", "int"))
    config = {
        name: ops.ConfigMeta(
            name=name, type=type_, default=None, description=None
        )
        for name, type_ in options
    }
    driver_cls = sunbeam_charm.OSCinderVolumeDriverOperatorCharm
    # _configuration_type_overrides only reads self.meta.config, so a
    # lightweight stand-in can replace a full charm instance
    driver = types.SimpleNamespace(meta=types.SimpleNamespace(config=config))
    overrides = driver_cls._configuration_type_overrides(driver)
    return storage.to_pydantic_class(config, overrides)


def _driver_config(**kwargs) -> pydantic.BaseModel:
    """Build a driver config that declares volume-dd-blocksize."""
    return _driver_config_class()(san_ip="10.0.0.1", **kwargs)


def _snap_data(config: pydantic.BaseModel) -> dict:
    """Dump the config as CinderVolumeConfigurationContext does."""
    return config.model_dump(by_alias=True, exclude_none=True, mode="json")


def test_volume_dd_blocksize_not_added_without_option():
    """A driver that does not declare the option gets no override."""
    config_class = _driver_config_class(declare_blocksize=False)

    assert "volume_dd_blocksize" not in config_class.model_fields


def test_volume_dd_blocksize_unset_is_not_sent_to_snap():
    """An unset block size is omitted so the snap default applies."""
    config = _driver_config()

    assert config.volume_dd_blocksize is None
    assert "volume-dd-blocksize" not in _snap_data(config)


@pytest.mark.parametrize("value", [512, 1048576])
def test_volume_dd_blocksize_accepts_512_or_more(value):
    """Block sizes of 512 bytes or more are sent to the snap."""
    config = _driver_config(volume_dd_blocksize=value)

    assert _snap_data(config)["volume-dd-blocksize"] == value


def test_volume_dd_blocksize_rejects_below_512():
    """Block sizes below 512 bytes fail validation, matching the snap."""
    with pytest.raises(pydantic.ValidationError, match="volume_dd_blocksize"):
        _driver_config(volume_dd_blocksize=256)
