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

"""Scenario (ops.testing state-transition) tests for neutron-baremetal-switch-config-k8s."""

from ops import (
    testing,
)

_SAMPLE_CONFIG = """driver = netconf-openconfig
device_params = name:nexus
switch_info = nexus
switch_id = 00:53:00:0a:0a:0a
host = nexus.example.net
username = user
"""

NEXUS_SAMPLE_CONFIG = "[nexus.example.net]\n" + _SAMPLE_CONFIG
KEY_LINE = "key_filename = /etc/neutron/sshkeys/nexus-sshkey"


class TestActiveWithValidConfig:
    """Config-changed with valid secret and relation → ActiveStatus."""

    def test_active_with_valid_config(self, ctx, valid_state):
        """Valid config and relation → active."""
        state_out = ctx.run(ctx.on.config_changed(), valid_state)
        assert state_out.unit_status == testing.ActiveStatus(
            "Provider is ready"
        )

    def test_active_with_key(self, ctx, valid_state_with_key):
        """Valid config with SSH key → active."""
        state_out = ctx.run(ctx.on.config_changed(), valid_state_with_key)
        assert state_out.unit_status == testing.ActiveStatus(
            "Provider is ready"
        )

    def test_relation_data_set(
        self, ctx, valid_secret, switch_config_relation
    ):
        """Config-changed writes secret ID to relation data."""
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": valid_secret.id},
            relations=[switch_config_relation],
            secrets=[valid_secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        rel = state_out.get_relation(switch_config_relation.id)
        assert rel.local_app_data.get("switch-config") == valid_secret.id


class TestBlockedNoConfig:
    """Config-changed without conf-secrets → BlockedStatus."""

    def test_blocked_empty_conf_secrets(self, ctx, switch_config_relation):
        """Empty conf-secrets → blocked."""
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": ""},
            relations=[switch_config_relation],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_secret_not_found(self, ctx, switch_config_relation):
        """Non-existent secret ID → blocked."""
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": "secret:nonexistent"},
            relations=[switch_config_relation],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedInvalidConfig:
    """Config-changed with invalid secret content → BlockedStatus."""

    def test_blocked_missing_conf_key(self, ctx, switch_config_relation):
        """Secret without 'conf' key → blocked."""
        secret = testing.Secret(
            tracked_content={"foo": "bar"},
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_malformed_config(self, ctx, switch_config_relation):
        """Malformed ini content → blocked."""
        secret = testing.Secret(
            tracked_content={"conf": "not valid ini ["},
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_unknown_fields(self, ctx, switch_config_relation):
        """Unknown config fields → blocked."""
        secret = testing.Secret(
            tracked_content={
                "conf": "\n".join([NEXUS_SAMPLE_CONFIG, "foo = 10"])
            },
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_invalid_key_filename_path(
        self, ctx, switch_config_relation
    ):
        """key_filename with wrong base path → blocked."""
        conf = "\n".join(
            [NEXUS_SAMPLE_CONFIG, 'key_filename = "/foo/nexus-sshkey"']
        )
        secret = testing.Secret(
            tracked_content={"conf": conf, "nexus-sshkey": "foo"},
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_missing_additional_file(
        self, ctx, switch_config_relation
    ):
        """key_filename references file not in secret → blocked."""
        secret = testing.Secret(
            tracked_content={
                "conf": "\n".join([NEXUS_SAMPLE_CONFIG, KEY_LINE]),
            },
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestDuplicateSections:
    """Duplicate config sections across secrets → BlockedStatus."""

    def test_blocked_duplicate_across_secrets(
        self, ctx, switch_config_relation
    ):
        """Same section in two different secrets → blocked."""
        secret_1 = testing.Secret(
            tracked_content={"conf": NEXUS_SAMPLE_CONFIG},
            owner="app",
        )
        secret_2 = testing.Secret(
            tracked_content={"conf": NEXUS_SAMPLE_CONFIG},
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": ",".join([secret_1.id, secret_2.id])},
            relations=[switch_config_relation],
            secrets=[secret_1, secret_2],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)

    def test_blocked_duplicate_within_secret(
        self, ctx, switch_config_relation
    ):
        """Same section repeated within one secret → blocked."""
        secret = testing.Secret(
            tracked_content={"conf": "\n".join([NEXUS_SAMPLE_CONFIG] * 2)},
            owner="app",
        )
        state_in = testing.State(
            leader=True,
            config={"conf-secrets": secret.id},
            relations=[switch_config_relation],
            secrets=[secret],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert isinstance(state_out.unit_status, testing.BlockedStatus)
