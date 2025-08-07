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

"""Charm the service.

Refer to the following tutorial that will help you
develop a new k8s charm using the Operator Framework:

https://juju.is/docs/sdk/create-a-minimal-kubernetes-charm
"""

import base64
import logging
import tempfile
from typing import (
    List,
)

import ops
import requests
from certs import (
    is_valid_chain,
    parse_cert_chain,
)
from charms.keystone_saml_k8s.v1.keystone_saml import (
    KeystoneSAMLProvider,
    KeystoneSAMLProviderChangedEvent,
)

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class KeystoneSamlK8SCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.saml_provider = KeystoneSAMLProvider(self)

        # Lifecycle events
        self.framework.observe(
            self.on.config_changed,
            self._on_config_changed,
        )
        self.framework.observe(
            self.on.keystone_saml_relation_joined,
            self._on_config_changed,
        )

        # keystone saml provider
        self.framework.observe(
            self.saml_provider.on.changed,
            self._on_saml_changed,
        )

        # Action events
        self.framework.observe(
            self.on.get_keystone_sp_urls_action,
            self._on_get_keystone_sp_urls,
        )

    def _on_saml_changed(
        self, event: KeystoneSAMLProviderChangedEvent
    ) -> None:
        if not self.saml_provider.requirer_data:
            self.unit.status = ops.WaitingStatus(
                "Waiting for the requirer charm to set SP urls"
            )
            return
        self.unit.status = ops.ActiveStatus("Provider is ready")

    def _on_get_keystone_sp_urls(self, event: ops.ActionEvent) -> None:
        urls = self.saml_provider.requirer_data
        if not urls:
            event.fail("No keystone SP urls found.")
            return
        event.set_results(urls)

    def _get_missing_config(self) -> List[str]:
        required = ["name", "label", "metadata-url"]
        missing = []
        for i in required:
            val = self.config.get(i, "")
            if not val:
                missing.append(i)
        return missing

    def _ensure_ca_chain_is_valid(self) -> bool:
        chain = self.config.get("ca-chain", "")
        if not chain:
            # not having a ca-chain is valid
            return True
        return is_valid_chain(chain)

    def _get_idp_metadata(self) -> str:
        metadata_url = self.config.get("metadata-url", "")
        if not metadata_url:
            return ""

        with tempfile.NamedTemporaryFile() as fd:
            verify = True
            cachain = self.config.get("ca-chain", "")
            if cachain:
                verify = fd.name
                data = base64.b64decode(cachain)
                fd.write(data)
                fd.flush()
            metadata = requests.get(metadata_url, verify=verify)
            metadata.raise_for_status()
        return metadata.text

    def _on_config_changed(self, event: ops.HookEvent) -> None:
        missing = self._get_missing_config()
        if missing:
            self.unit.status = ops.BlockedStatus(
                f"Missing required config(s): {', '.join(missing)}"
            )
            return

        if not self._ensure_ca_chain_is_valid():
            self.unit.status = ops.BlockedStatus("Invalid ca-chain in config")
            return

        try:
            metadata = self._get_idp_metadata()
        except Exception as e:
            logger.error(f"failed to get metadata: {e}")
            self.unit.status = ops.BlockedStatus("Failed to get IDP metadata")
            return

        try:
            ca_chain = []
            config_chain = self.config.get("ca-chain", "")
            if config_chain:
                ca_chain = parse_cert_chain(
                    base64.b64decode(config_chain).decode()
                )
        except Exception as e:
            logger.error(f"failed to parse ca chain: {e}")
            self.unit.status = ops.BlockedStatus(
                "Failed parse configured CA chain"
            )
            return

        rel_data = {
            "metadata": metadata,
            "name": self.config["name"],
            "label": self.config["label"],
            "ca_chain": ca_chain,
        }
        if not self.saml_provider.requirer_data:
            self.unit.status = ops.WaitingStatus(
                "Waiting for keystone to set SP URLs"
            )
        else:
            self.unit.status = ops.ActiveStatus("Provider is ready")
        self.saml_provider.set_provider_info(rel_data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(KeystoneSamlK8SCharm)
