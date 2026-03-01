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

"""Scenario (ops.testing state-transition) tests for keystone-saml-k8s."""

import base64
import json
from unittest import (
    mock,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
)

VALID_CONFIG = {
    "name": "test-provider",
    "label": "Test Provider",
    "metadata-url": "https://example.com/metadata",
}

SP_URLS = {
    "acs-url": "https://keystone.example.com/acs",
    "logout-url": "https://keystone.example.com/logout",
    "metadata-url": "https://keystone.example.com/metadata",
}


def keystone_saml_relation(
    requirer_data: dict | None = None,
) -> testing.Relation:
    """Build a keystone-saml relation with optional requirer (remote app) data."""
    return testing.Relation(
        endpoint="keystone-saml",
        interface="keystone_saml",
        remote_app_name="keystone",
        remote_app_data=requirer_data or {},
    )


def _mock_requests_get():
    """Return a mock for requests.get that returns test metadata."""
    mock_response = mock.MagicMock()
    mock_response.text = "<xml>test metadata</xml>"
    return mock.patch("charm.requests.get", return_value=mock_response)


class TestMissingConfig:
    """Charm should block when required config options are missing."""

    def test_blocked_missing_all_config(self, ctx):
        """No config set → blocked with 'Missing required config'."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", "Missing required config")

    @pytest.mark.parametrize("missing_key", ["name", "label", "metadata-url"])
    def test_blocked_missing_each_config(self, ctx, missing_key):
        """Missing a single required config key → blocked."""
        config = {k: v for k, v in VALID_CONFIG.items() if k != missing_key}
        state_in = testing.State(leader=True, config=config)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", missing_key)


class TestValidConfigNoRelation:
    """With valid config but no relation, charm waits for keystone."""

    def test_waiting_for_keystone(self, ctx):
        """Valid config, no relation → waiting for keystone SP URLs."""
        with _mock_requests_get():
            state_in = testing.State(leader=True, config=VALID_CONFIG)
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "Waiting for keystone")


class TestValidConfigWithRelation:
    """With valid config and a complete relation, charm reaches active."""

    def test_active_with_sp_urls(self, ctx):
        """Valid config + requirer SP URLs → active."""
        relation = keystone_saml_relation(SP_URLS)
        with _mock_requests_get():
            state_in = testing.State(
                leader=True, config=VALID_CONFIG, relations=[relation]
            )
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "active", "Provider is ready")

    def test_waiting_without_sp_urls(self, ctx):
        """Valid config + relation but no requirer data → waiting."""
        relation = keystone_saml_relation()
        with _mock_requests_get():
            state_in = testing.State(
                leader=True, config=VALID_CONFIG, relations=[relation]
            )
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "Waiting for keystone")

    def test_relation_data_set(self, ctx):
        """Config-changed sets provider data on the relation."""
        relation = keystone_saml_relation(SP_URLS)
        with _mock_requests_get():
            state_in = testing.State(
                leader=True, config=VALID_CONFIG, relations=[relation]
            )
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        rel_out = state_out.get_relation(relation.id)
        local_app_data = rel_out.local_app_data
        assert local_app_data["name"] == "test-provider"
        assert local_app_data["label"] == "Test Provider"
        decoded_metadata = base64.b64decode(
            local_app_data["metadata"]
        ).decode()
        assert decoded_metadata == "<xml>test metadata</xml>"
        ca_chain = json.loads(local_app_data["ca_chain"])
        assert ca_chain == []


class TestValidCaChainRelationData:
    """When a valid CA chain is configured, relation data includes parsed certs."""

    def test_relation_data_includes_ca_chain(self, ctx):
        """Valid CA chain → relation data contains parsed certificate list."""
        from ops_sunbeam.test_utils import (
            TEST_CA,
        )

        ca_chain_b64 = base64.b64encode(TEST_CA.encode()).decode()
        config = {**VALID_CONFIG, "ca-chain": ca_chain_b64}
        relation = keystone_saml_relation(SP_URLS)
        with _mock_requests_get(), mock.patch(
            "charm.is_valid_chain", return_value=True
        ):
            state_in = testing.State(
                leader=True, config=config, relations=[relation]
            )
            state_out = ctx.run(ctx.on.config_changed(), state_in)

        rel_out = state_out.get_relation(relation.id)
        local_app_data = rel_out.local_app_data
        ca_chain = json.loads(local_app_data["ca_chain"])
        assert isinstance(ca_chain, list)
        assert len(ca_chain) == 1
        assert ca_chain[0] == TEST_CA


class TestInvalidCaChain:
    """Charm should block when the CA chain config is invalid."""

    def test_blocked_invalid_ca_chain(self, ctx):
        """Invalid ca-chain → blocked."""
        config = {
            **VALID_CONFIG,
            "ca-chain": base64.b64encode(b"invalid-chain").decode(),
        }
        with mock.patch("charm.is_valid_chain", return_value=False):
            state_in = testing.State(leader=True, config=config)
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", "Invalid ca-chain")


class TestMetadataFetchError:
    """Charm should block when IDP metadata fetch fails."""

    def test_blocked_metadata_error(self, ctx):
        """Metadata fetch raises → blocked with 'Failed to get IDP metadata'."""
        with mock.patch(
            "charm.requests.get", side_effect=Exception("Network error")
        ):
            state_in = testing.State(leader=True, config=VALID_CONFIG)
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "blocked", "Failed to get IDP metadata")


class TestCaChainParseError:
    """Charm should block when CA chain parsing fails."""

    def test_blocked_ca_parse_error(self, ctx):
        """Malformed cert in ca-chain → blocked."""
        invalid_b64 = base64.b64encode(
            b"-----BEGIN CERTIFICATE-----\nINVALID\n-----END CERTIFICATE-----"
        ).decode()
        config = {**VALID_CONFIG, "ca-chain": invalid_b64}
        with _mock_requests_get(), mock.patch(
            "charm.is_valid_chain", return_value=True
        ):
            state_in = testing.State(leader=True, config=config)
            state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(
            state_out, "blocked", "Failed parse configured CA chain"
        )


class TestGetKeystoneSpUrlsAction:
    """Tests for the get-keystone-sp-urls action."""

    def test_action_fails_no_urls(self, ctx):
        """Action without relation data → fails."""
        state_in = testing.State(leader=True)
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("get-keystone-sp-urls"), state_in)
        assert "No keystone SP urls found" in str(exc_info.value)

    def test_action_returns_urls(self, ctx):
        """Action with requirer data → returns SP URLs."""
        relation = keystone_saml_relation(SP_URLS)
        state_in = testing.State(leader=True, relations=[relation])
        ctx.run(ctx.on.action("get-keystone-sp-urls"), state_in)
        results = ctx.action_results
        assert results["acs-url"] == "https://keystone.example.com/acs"
        assert results["logout-url"] == "https://keystone.example.com/logout"
        assert (
            results["metadata-url"] == "https://keystone.example.com/metadata"
        )


class TestSamlChangedEvent:
    """Tests for the keystone-saml relation-changed handling."""

    def test_active_when_requirer_sets_data(self, ctx):
        """Relation-changed with requirer data → active."""
        relation = keystone_saml_relation(SP_URLS)
        state_in = testing.State(leader=True, relations=[relation])
        state_out = ctx.run(ctx.on.relation_changed(relation), state_in)
        assert_unit_status(state_out, "active", "Provider is ready")

    def test_no_status_change_when_no_requirer_data(self, ctx):
        """Relation-changed without requirer data → no status change (library skips empty data)."""
        relation = keystone_saml_relation()
        state_in = testing.State(leader=True, relations=[relation])
        state_out = ctx.run(ctx.on.relation_changed(relation), state_in)
        # Library logs "No requirer relation data available" and returns
        # without emitting changed event, so charm doesn't update status.
        assert state_out.unit_status == testing.UnknownStatus()
