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

"""Scenario (ops.testing state-transition) tests for neutron-generic-switch-config-k8s."""

from ops import (
    testing,
)

_SAMPLE_CONFIG = """[genericswitch:%(name)s-hostname]
device_type = %(device_type)s
ngs_mac_address = 00:53:00:0a:0a:0a
ip = 10.20.30.40
username = admin
"""


def _get_sample_config(
    name: str, device_type: str, with_key: bool = True
) -> str:
    config = _SAMPLE_CONFIG % {"name": name, "device_type": device_type}
    if with_key:
        config = config + "\nkey_file = /etc/neutron/sshkeys/%s-key" % name
    return config


ARISTA_CONFIG = _get_sample_config("arista", "netmiko_arista_eos")
ARISTA_CONFIG_NO_KEY = _get_sample_config(
    "arista", "netmiko_arista_eos", with_key=False
)


class TestActiveWithValidConfig:
    """Config-changed with valid secret and relation → ActiveStatus."""

    def test_active_with_valid_config(self, ctx, valid_state):
        """Valid config and relation → active."""
        state_out = ctx.run(ctx.on.config_changed(), valid_state)
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

    def test_blocked_section_not_genericswitch(
        self, ctx, switch_config_relation
    ):
        """Section name not starting with 'genericswitch:' → blocked."""
        conf = ARISTA_CONFIG.replace("genericswitch:arista", "arista")
        secret = testing.Secret(
            tracked_content={"conf": conf, "arista-key": "foo"},
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

    def test_blocked_missing_device_type(self, ctx, switch_config_relation):
        """Missing device_type field → blocked."""
        conf = ARISTA_CONFIG.replace("netmiko_arista_eos", "")
        secret = testing.Secret(
            tracked_content={"conf": conf, "arista-key": "foo"},
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
                "conf": "\n".join([ARISTA_CONFIG, "foo = 10"]),
                "arista-key": "foo",
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

    def test_blocked_invalid_key_file_path(self, ctx, switch_config_relation):
        """key_file with wrong base path → blocked."""
        conf = "\n".join(
            [ARISTA_CONFIG_NO_KEY, 'key_file = "/foo/arista-key"']
        )
        secret = testing.Secret(
            tracked_content={"conf": conf, "arista-key": "foo"},
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
        """key_file references file not in secret → blocked."""
        secret = testing.Secret(
            tracked_content={"conf": ARISTA_CONFIG},
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
            tracked_content={"conf": ARISTA_CONFIG, "arista-key": "foo"},
            owner="app",
        )
        secret_2 = testing.Secret(
            tracked_content={"conf": ARISTA_CONFIG, "arista-key": "foo"},
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
            tracked_content={
                "conf": "\n".join([ARISTA_CONFIG] * 2),
                "arista-key": "foo",
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
