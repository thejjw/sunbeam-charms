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

"""Helper functions to verify CA chains."""

import re
from typing import (
    List,
)

from cryptography import (
    x509,
)
from cryptography.hazmat.backends import (
    default_backend,
)


def parse_cert_chain(pem_data: str) -> List[str]:
    """Return a list of pem certs from a combined pem file."""
    parsed_certs = []
    if not pem_data:
        return []

    ca_chain = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        pem_data,
        re.DOTALL,
    )

    for idx, pem_cert in enumerate(ca_chain):
        try:
            x509.load_pem_x509_certificate(
                pem_cert.encode(), default_backend()
            )
            parsed_certs.append(pem_cert)
        except Exception as e:
            raise ValueError(
                f"Certificate #{idx + 1} is corrupted or invalid: {e}"
            )

    return parsed_certs


def is_valid_chain(chain: str) -> bool:
    """Return true if the CA chain PEM is valid."""
    try:
        parsed_chain = parse_cert_chain(chain)
    except ValueError:
        return False
    if not parsed_chain:
        return False
    return True
