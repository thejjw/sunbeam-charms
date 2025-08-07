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

"""Define keystone-saml-k8s tests."""

import base64
import json
import unittest.mock as mock

import charm
import ops_sunbeam.test_utils as test_utils
from ops.testing import (
    ActionFailed,
    Harness,
)


class TestKeystoneSamlK8SCharm(test_utils.CharmTestCase):
    """Test Keystone SAML charm."""

    PATCHES = []

    def setUp(self):
        """Run test setup."""
        super().setUp(charm, self.PATCHES)
        self.harness = Harness(charm.KeystoneSamlK8SCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def add_keystone_saml_relation(self) -> int:
        """Add keystone-saml relation."""
        rel_id = self.harness.add_relation("keystone-saml", "keystone")
        self.harness.add_relation_unit(rel_id, "keystone/0")
        return rel_id

    def test_missing_config(self):
        """Test charm with missing configuration."""
        self.harness.set_leader()

        # Trigger config changed without setting config
        self.harness.charm.on.config_changed.emit()

        # Should be in BlockedStatus due to missing config
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.BlockedStatus
        )
        self.assertIn(
            "Missing required config",
            str(self.harness.charm.unit.status.message),
        )

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_valid_config_no_relation(self, mock_is_valid, mock_get):
        """Test charm with valid config but no relation."""
        self.harness.set_leader()
        mock_is_valid.return_value = True

        # Mock the metadata response
        mock_response = mock.MagicMock()
        mock_response.text = "<xml>test metadata</xml>"
        mock_get.return_value = mock_response

        # Set valid configuration
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
            }
        )

        # Should be waiting for keystone
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.WaitingStatus
        )
        self.assertIn(
            "Waiting for keystone",
            str(self.harness.charm.unit.status.message),
        )

        # Verify metadata was fetched
        mock_get.assert_called_once_with(
            "https://example.com/metadata", verify=True
        )

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_valid_config_with_relation(self, mock_is_valid, mock_get):
        """Test charm with valid config and relation."""
        self.harness.set_leader()
        mock_is_valid.return_value = True

        # Mock the metadata response
        mock_response = mock.MagicMock()
        mock_response.text = "<xml>test metadata</xml>"
        mock_get.return_value = mock_response

        # Add relation with SP URLs
        rel_id = self.add_keystone_saml_relation()
        self.harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "acs-url": "https://keystone.example.com/acs",
                "logout-url": "https://keystone.example.com/logout",
                "metadata-url": "https://keystone.example.com/metadata",
            },
        )

        # Set valid configuration
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
            }
        )

        # Should be active
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.ActiveStatus
        )
        self.assertEqual(
            "Provider is ready",
            str(self.harness.charm.unit.status.message),
        )

    @mock.patch("charm.is_valid_chain")
    def test_invalid_ca_chain(self, mock_is_valid):
        """Test charm with invalid CA chain."""
        self.harness.set_leader()
        mock_is_valid.return_value = False

        # Set config with invalid CA chain
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
                "ca-chain": base64.b64encode(b"invalid-chain").decode(),
            }
        )

        # Should be blocked due to invalid CA chain
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.BlockedStatus
        )
        self.assertEqual(
            "Invalid ca-chain in config",
            str(self.harness.charm.unit.status.message),
        )

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_metadata_fetch_error(self, mock_is_valid, mock_get):
        """Test charm when metadata fetch fails."""
        self.harness.set_leader()
        mock_is_valid.return_value = True
        mock_get.side_effect = Exception("Network error")

        # Set valid configuration
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
            }
        )

        # Should be blocked due to metadata fetch error
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.BlockedStatus
        )
        self.assertEqual(
            "Failed to get IDP metadata",
            str(self.harness.charm.unit.status.message),
        )

    def test_get_keystone_sp_urls_action_no_urls(self):
        """Test get-keystone-sp-urls action without URLs."""
        self.harness.set_leader()

        # Run the action - should fail since no relation data
        with self.assertRaises(ActionFailed) as context:
            self.harness.run_action("get-keystone-sp-urls")

        self.assertEqual("No keystone SP urls found.", str(context.exception))

    def test_get_keystone_sp_urls_action_with_urls(self):
        """Test get-keystone-sp-urls action with URLs."""
        self.harness.set_leader()

        # Add relation with SP URLs
        rel_id = self.add_keystone_saml_relation()
        self.harness.update_relation_data(
            rel_id,
            "keystone",
            {
                "acs-url": "https://keystone.example.com/acs",
                "logout-url": "https://keystone.example.com/logout",
                "metadata-url": "https://keystone.example.com/metadata",
            },
        )

        # Run the action
        action_event = self.harness.run_action("get-keystone-sp-urls")

        # Should return the URLs
        results = action_event.results
        self.assertEqual(
            "https://keystone.example.com/acs", results.get("acs-url")
        )
        self.assertEqual(
            "https://keystone.example.com/logout", results.get("logout-url")
        )
        self.assertEqual(
            "https://keystone.example.com/metadata",
            results.get("metadata-url"),
        )

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_config_changed_with_valid_ca_chain(self, mock_is_valid, mock_get):
        """Test config changed with valid CA chain sets relation data."""
        self.harness.set_leader()
        mock_is_valid.return_value = True

        # Mock the metadata response
        mock_response = mock.MagicMock()
        mock_response.text = "<xml>test metadata</xml>"
        mock_get.return_value = mock_response

        # Add relation
        rel_id = self.add_keystone_saml_relation()

        # Create base64 encoded CA chain (using test certificate)
        ca_chain_bytes = test_utils.TEST_CA.encode()
        ca_chain_b64 = base64.b64encode(ca_chain_bytes).decode()

        # Set configuration with CA chain
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
                "ca-chain": ca_chain_b64,
            }
        )

        # Verify relation data was set with parsed CA chain
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.app.name
        )
        self.assertEqual("test-provider", rel_data.get("name"))
        self.assertEqual("Test Provider", rel_data.get("label"))
        # Metadata is base64 encoded in relation data
        self.assertEqual(
            "<xml>test metadata</xml>",
            base64.b64decode(rel_data.get("metadata")).decode(),
        )

        # CA chain should be JSON-serialized list of PEM certificates
        ca_chain_str = rel_data.get("ca_chain")
        self.assertIsNotNone(ca_chain_str)
        ca_chain = json.loads(ca_chain_str)
        self.assertIsInstance(ca_chain, list)
        self.assertEqual(len(ca_chain), 1)
        self.assertEqual(ca_chain[0], test_utils.TEST_CA)

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_config_changed_without_ca_chain(self, mock_is_valid, mock_get):
        """Test config changed without CA chain sets empty ca_chain list."""
        self.harness.set_leader()
        mock_is_valid.return_value = True

        # Mock the metadata response
        mock_response = mock.MagicMock()
        mock_response.text = "<xml>test metadata</xml>"
        mock_get.return_value = mock_response

        # Add relation
        rel_id = self.add_keystone_saml_relation()

        # Set configuration without CA chain
        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
            }
        )

        # Verify relation data was set with empty CA chain
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.app.name
        )
        ca_chain_str = rel_data.get("ca_chain")
        self.assertIsNotNone(ca_chain_str)
        # CA chain should be JSON-serialized empty list
        import json

        ca_chain = json.loads(ca_chain_str)
        self.assertIsInstance(ca_chain, list)
        self.assertEqual(len(ca_chain), 0)

    @mock.patch("charm.requests.get")
    @mock.patch("charm.is_valid_chain")
    def test_config_changed_ca_chain_parse_error(
        self, mock_is_valid, mock_get
    ):
        """Test config changed with CA chain parse error."""
        self.harness.set_leader()
        mock_is_valid.return_value = True

        # Mock the metadata response
        mock_response = mock.MagicMock()
        mock_response.text = "<xml>test metadata</xml>"
        mock_get.return_value = mock_response

        # Set config with malformed base64 CA chain that will cause
        # parse error. This creates a malformed certificate that will
        # fail when parse_cert_chain tries to validate it
        invalid_b64 = base64.b64encode(
            b"-----BEGIN CERTIFICATE-----\nINVALID\n-----END CERTIFICATE-----"
        ).decode()

        self.harness.update_config(
            {
                "name": "test-provider",
                "label": "Test Provider",
                "metadata-url": "https://example.com/metadata",
                "ca-chain": invalid_b64,
            }
        )

        # Should be blocked due to CA chain parse error
        self.assertIsInstance(
            self.harness.charm.unit.status, charm.ops.BlockedStatus
        )
        self.assertEqual(
            "Failed parse configured CA chain",
            str(self.harness.charm.unit.status.message),
        )
