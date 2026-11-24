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

"""Scenario (ops.testing state-transition) tests for keystone-k8s."""

import configparser
import dataclasses
from pathlib import (
    Path,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_config_file_exists,
    assert_container_disconnect_causes_waiting_or_blocked,
    assert_relation_broken_causes_blocked_or_waiting,
    assert_unit_status,
    cleanup_database_requires_events,
    k8s_api_container,
    mandatory_relations_from_charmcraft,
    valkey_credentials_secret,
    valkey_relation_complete,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)


class TestAllRelations:
    """With all relations complete the charm reaches active and configures the service."""

    def test_active_with_all_relations(self, ctx, complete_state):
        """Config-changed with all relations → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_file_written(self, ctx, complete_state):
        """All relations present → keystone.conf is rendered."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert_config_file_exists(
            state_out, ctx, "keystone", "/etc/keystone/keystone.conf"
        )


class TestPebbleReady:
    """Pebble-ready event with all relations → container configured."""

    def test_pebble_ready_configures_container(self, ctx, complete_state):
        """Pebble-ready adds a layer and starts the WSGI service."""
        container = complete_state.get_container("keystone")
        state_out = ctx.run(ctx.on.pebble_ready(container), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")

        out_container = state_out.get_container("keystone")
        assert "keystone" in out_container.layers
        layer = out_container.layers["keystone"]
        assert "wsgi-keystone" in layer.to_dict().get("services", {})

        assert out_container.service_statuses.get("wsgi-keystone") == (
            testing.pebble.ServiceStatus.ACTIVE
        )

    def test_pebble_ready_without_relations_blocked(self, ctx, storages):
        """Pebble-ready but no relations → blocked."""
        container = k8s_api_container("keystone")
        state_in = testing.State(
            leader=True, containers=[container], storages=storages
        )
        state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

        assert isinstance(state_out.unit_status, testing.BlockedStatus)


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked."""

    def test_blocked_when_no_relations(self, ctx, storages):
        """No relations at all → blocked with 'integration missing'."""
        container = k8s_api_container("keystone", can_connect=False)
        state_in = testing.State(
            leader=True, containers=[container], storages=storages
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert_unit_status(state_out, "blocked", "integration missing")


class TestBlockedWhenEachRelationMissing:
    """Parametrized: removing each mandatory relation one at a time → blocked/waiting."""

    @pytest.mark.parametrize(
        "missing_rel",
        sorted(MANDATORY_RELATIONS),
    )
    def test_blocked_when_relation_missing(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        container,
        storages,
        missing_rel,
    ):
        """Charm should be blocked/waiting when a mandatory relation is removed."""
        remaining = [
            r for r in complete_relations if r.endpoint != missing_rel
        ]
        state_in = testing.State(
            leader=True,
            relations=remaining,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader unit waits for leader to bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert isinstance(state_out.unit_status, testing.WaitingStatus)
        assert "Leader not ready" in state_out.unit_status.message


class TestContainerDisconnectBlocksOrWaits:
    """Config-changed with disconnected containers → blocked/waiting."""

    def test_container_disconnect(self, ctx, complete_state):
        """Charm should block/wait when containers cannot connect."""
        assert_container_disconnect_causes_waiting_or_blocked(
            ctx, complete_state
        )


class TestRelationBrokenBlocksOrWaits:
    """Breaking each mandatory relation → blocked/waiting."""

    @pytest.mark.parametrize(
        "relation_endpoint",
        sorted(MANDATORY_RELATIONS),
    )
    def test_relation_broken(self, ctx, complete_state, relation_endpoint):
        """Charm should block/wait when a mandatory relation is broken."""
        assert_relation_broken_causes_blocked_or_waiting(
            ctx, complete_state, relation_endpoint
        )


KEYSTONE_CONF = "/etc/keystone/keystone.conf"
VALKEY_CA_FILE = "/etc/keystone/distributed-cache-ca.pem"
VALKEY_CA = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBvalkeytestca\n"
    "-----END CERTIFICATE-----"
)


def _cache_section(state_out, ctx) -> dict:
    """Return the [cache] section of the rendered keystone.conf."""
    path = assert_config_file_exists(state_out, ctx, "keystone", KEYSTONE_CONF)
    parser = configparser.RawConfigParser(strict=False)
    parser.read_string(path.read_text())
    return dict(parser["cache"])


def _ca_file(state_out, ctx) -> str:
    """Return the content of the rendered Valkey CA file."""
    path = assert_config_file_exists(
        state_out, ctx, "keystone", VALKEY_CA_FILE
    )
    return path.read_text().strip()


def _with_valkey(state, relation, secret=None):
    """Return a copy of state with the Valkey relation (and secret) added."""
    secret = secret or valkey_credentials_secret()
    return dataclasses.replace(
        state,
        relations=[*state.relations, relation],
        secrets=[*state.secrets, secret],
    )


class TestDistributedCache:
    """Optional distributed-cache (Valkey) relation renders oslo.cache."""

    def test_no_relation_no_cache_backend(self, ctx, complete_state):
        """Without the relation no cache backend is configured."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        assert state_out.unit_status == testing.ActiveStatus("")
        cache = _cache_section(state_out, ctx)
        assert "backend" not in cache
        assert "enabled" not in cache
        # Bug #1899117: expiration_time must stay in [cache].
        assert "expiration_time" in cache
        assert _ca_file(state_out, ctx) == ""

    def test_relation_tls_off_direct_backend(self, ctx, complete_state):
        """Client TLS off → direct connection to the primary Service."""
        state_in = _with_valkey(complete_state, valkey_relation_complete())
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        cache = _cache_section(state_out, ctx)
        assert cache["enabled"] == "true"
        assert cache["backend"] == "dogpile.cache.redis"
        assert (
            cache["redis_server"] == "valkey-primary.m.svc.cluster.local:6379"
        )
        assert cache["username"] == "relation-5-abc"
        assert cache["password"] == "s3cret"
        assert "redis_sentinels" not in cache
        assert "tls_enabled" not in cache
        assert "expiration_time" in cache
        assert _ca_file(state_out, ctx) == ""

    def test_relation_tls_on_sentinel_backend(self, ctx, complete_state):
        """Client TLS on → Sentinel discovery and the Valkey CA file."""
        relation = valkey_relation_complete(tls=True, tls_ca=VALKEY_CA)
        state_in = _with_valkey(complete_state, relation)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        cache = _cache_section(state_out, ctx)
        assert cache["backend"] == "dogpile.cache.redis_sentinel"
        assert cache["redis_sentinels"] == ",".join(
            f"valkey-{i}.valkey-endpoints.m.svc.cluster.local:26380"
            for i in range(3)
        )
        assert cache["redis_sentinel_service_name"] == "primary"
        assert cache["tls_enabled"] == "true"
        assert cache["tls_cafile"] == VALKEY_CA_FILE
        assert "redis_server" not in cache
        assert "expiration_time" in cache
        assert _ca_file(state_out, ctx) == VALKEY_CA

    def test_relation_without_credentials_no_cache(self, ctx, complete_state):
        """Endpoints without credentials → waiting, no backend rendered."""
        relation = valkey_relation_complete()
        data = dict(relation.remote_app_data)
        del data["secret-user"]
        relation = dataclasses.replace(relation, remote_app_data=data)
        state_in = dataclasses.replace(
            complete_state, relations=[*complete_state.relations, relation]
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.WaitingStatus(
            "(distributed-cache) integration incomplete"
        )
        assert "backend" not in _cache_section(state_out, ctx)

    def test_relation_changed_rerenders(self, ctx, complete_state):
        """Relation data changing from TLS off to on re-renders the config."""
        state_in = _with_valkey(complete_state, valkey_relation_complete())
        state_mid = ctx.run(ctx.on.config_changed(), state_in)
        assert (
            _cache_section(state_mid, ctx)["backend"] == "dogpile.cache.redis"
        )
        # The database library defines its events per run; reset them
        # before running the charm a second time in the same test.
        cleanup_database_requires_events()

        old = state_mid.get_relations("distributed-cache")[0]
        new_data = valkey_relation_complete(
            tls=True, tls_ca=VALKEY_CA
        ).remote_app_data
        relation = dataclasses.replace(old, remote_app_data=new_data)
        state_tls = dataclasses.replace(
            state_mid,
            relations=[r for r in state_mid.relations if r.id != old.id]
            + [relation],
        )
        state_out = ctx.run(ctx.on.relation_changed(relation), state_tls)

        cache = _cache_section(state_out, ctx)
        assert cache["backend"] == "dogpile.cache.redis_sentinel"
        assert cache["tls_cafile"] == VALKEY_CA_FILE
        assert _ca_file(state_out, ctx) == VALKEY_CA

    def test_relation_broken_removes_cache(self, ctx, complete_state):
        """Removing the optional relation drops the backend, unit stays active."""
        relation = valkey_relation_complete()
        state_in = _with_valkey(complete_state, relation)
        state_out = ctx.run(ctx.on.relation_broken(relation), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")
        cache = _cache_section(state_out, ctx)
        assert "backend" not in cache
        assert "expiration_time" in cache

    def test_secret_changed_updates_password(self, ctx, complete_state):
        """A rotated Valkey password is rendered on secret-changed."""
        secret = dataclasses.replace(
            valkey_credentials_secret(),
            latest_content={"username": "relation-5-abc", "password": "n3w"},
        )
        state_in = _with_valkey(
            complete_state, valkey_relation_complete(), secret
        )
        state_out = ctx.run(ctx.on.secret_changed(secret), state_in)

        assert _cache_section(state_out, ctx)["password"] == "n3w"

    def test_distributed_cache_is_optional(self):
        """The relation must never be treated as mandatory."""
        assert "distributed-cache" not in MANDATORY_RELATIONS
