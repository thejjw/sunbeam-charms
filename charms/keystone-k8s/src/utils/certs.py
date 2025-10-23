# Copyright 2024 Canonical Ltd.
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

"""Helper functions to verify certificates."""

# Helper functions are picked from
# https://github.com/canonical/manual-tls-certificates-operator/blob/main/src/helpers.py

import logging
import re
from typing import (
    List,
)

from cryptography import (
    x509,
)
from cryptography.exceptions import (
    InvalidSignature,
)
from cryptography.hazmat.backends import (
    default_backend,
)
from cryptography.hazmat.primitives import (
    serialization,
)

logger = logging.getLogger(__name__)


def cert_and_key_match(certificate: bytes, key: bytes) -> bool:
    """Checks if the supplied cert is derived from the supplied key."""
    crt = x509.load_pem_x509_certificate(certificate, default_backend())
    cert_pub_key = crt.public_key()

    private_key = serialization.load_pem_private_key(
        key, password=None, backend=default_backend()
    )
    private_public_key = private_key.public_key()
    return cert_pub_key.public_numbers() == private_public_key.public_numbers()


def certificate_is_valid(certificate: bytes) -> bool:
    """Returns whether a certificate is valid.

    Args:
        certificate: Certificate in bytes

    Returns:
        bool: True/False
    """
    try:
        x509.load_pem_x509_certificate(certificate)
        return True
    except ValueError:
        return False


def parse_ca_chain(ca_chain_pem: str) -> List[str]:
    """Returns list of certificates based on a PEM CA Chain file.

    Args:
        ca_chain_pem (str): String containing list of certificates. This string should look like:
            -----BEGIN CERTIFICATE-----
            <cert 1>
            -----END CERTIFICATE-----
            -----BEGIN CERTIFICATE-----
            <cert 2>
            -----END CERTIFICATE-----

    Returns:
        list: List of certificates
    """
    chain_list = re.findall(
        pattern="(?=-----BEGIN CERTIFICATE-----)(.*?)(?<=-----END CERTIFICATE-----)",
        string=ca_chain_pem,
        flags=re.DOTALL,
    )
    if not chain_list:
        raise ValueError("No certificate found in chain file")
    return chain_list


def ca_chain_is_valid(ca_chain: List[str]) -> bool:
    """Returns whether a ca chain is valid.

    It uses the x509 certificate method verify_directly_issued_by, which checks
    the certificate issuer name matches the issuer subject name and that
    the certificate is signed by the issuer's private key.

    Args:
        ca_chain: composed by a list of certificates.

    Returns:
        whether the ca chain is valid.
    """
    try:
        if len(ca_chain) < 2:
            # Just validate individual certs
            for cert in ca_chain:
                x509.load_pem_x509_certificate(cert.encode())

            return True

        # Check if the chain is in correct order
        for i in range(len(ca_chain) - 1):
            cert = x509.load_pem_x509_certificate(ca_chain[i].encode("utf-8"))
            issuer = x509.load_pem_x509_certificate(
                ca_chain[i + 1].encode("utf-8")
            )
            cert.verify_directly_issued_by(issuer)
    except (ValueError, TypeError, InvalidSignature) as e:
        logger.warning("Invalid CA chain: %s", e)
        return False

    return True
