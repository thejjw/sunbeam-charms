# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import atexit
import logging
from pathlib import (
    Path,
)
import shutil
import tempfile
import time

import hvac
import jubilant
import zaza.model


def _wait_for_vault_initialization(status: jubilant.Status):
    vault = status.apps.get("vault")
    if not vault:
        return False
    return (
        vault.app_status.message.startswith("Please initialize")
        and vault.app_status.current == "blocked"
    )


def initialize_vault_at_model(model: str = "secondary-controller:os-secondary"):
    """Initialize and unseal Vault, then authorize it with Juju.

    Args:
        model: The juju model where vault is deployed
        output_dir: Directory to save vault initialization data (optional)
    """
    output_dir = Path(tempfile.mkdtemp())
    atexit.register(lambda: shutil.rmtree(output_dir))

    juju = jubilant.Juju(model=model)
    status = juju.status()
    if status.apps["vault"].app_status.current == "active":
        logging.info("Vault is already active, skipping initialization")
        return
    juju.wait(
        _wait_for_vault_initialization,
        timeout=900,
    )
    juju.wait(lambda status: jubilant.all_blocked(status, "vault"), timeout=900)
    logging.info("Waiting for vault to be ready for initialization")

    status = juju.status()

    # Get vault address
    logging.info("Getting vault address")
    vault_address = status.apps["vault"].address
    vault_url = f"https://{vault_address}:8200"
    logging.info(f"Vault URL: {vault_url}")

    # Get vault CA certificate
    logging.info("Getting vault CA certificate")
    vault_secrets = juju.secrets()
    vault_secret = None
    for secret in vault_secrets:
        if secret.label == "self-signed-vault-ca-certificate":
            vault_secret = secret
            break

    if not vault_secret:
        raise RuntimeError("Could not find self-signed-vault-ca-certificate secret")

    logging.info(f"Found certificate secret: {vault_secret.uri}")
    cert_output = juju.show_secret(vault_secret.uri, reveal=True)

    certificate = cert_output.content["certificate"]

    # Save certificate to file
    cert_file = output_dir / "vault.pem"
    cert_file.write_text(certificate)
    logging.info(f"Saved certificate to {cert_file}")

    # Initialize vault using hvac
    logging.info("Initializing vault")
    client = hvac.Client(url=vault_url, verify=str(cert_file))

    if client.is_initialized() or not client.is_sealed():
        raise RuntimeError(
            "Vault is already initialized or unsealed, cannot continue with initialization"
        )

    init_response = client.initialize(secret_shares=1, secret_threshold=1)

    root_token = init_response["root_token"]
    unseal_key = init_response["keys"][0]
    # Authenticate with root token
    client.token = root_token
    # Unseal vault
    logging.info("Unsealing vault")
    client.unseal(unseal_key)

    for _ in range(60):
        ha_status = client.ha_status
        if ha_status.get("is_self"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Vault did not become active in time")

    # Create one-time token
    logging.info("Creating one-time token")
    token_response = client.create_token(ttl="10m")
    one_time_token: str = token_response["auth"]["client_token"]

    logging.info(f"One-time token: {one_time_token}")

    logging.info("Adding one-time token as juju secret")
    one_time_token_secret = juju.add_secret("one-time-token", {"token": one_time_token})

    logging.info("Granting secret to vault")
    juju.grant_secret(one_time_token_secret, "vault")

    logging.info("Authorizing vault charm")
    juju.run(
        "vault/0",
        "authorize-charm",
        {"secret-id": one_time_token_secret.unique_identifier},
    )

    logging.info("Removing one-time token secret")
    juju.remove_secret(one_time_token_secret)

    logging.info("Vault initialization complete")


def initialize_vault():
    initialize_vault_at_model(model=zaza.model.get_juju_model())
