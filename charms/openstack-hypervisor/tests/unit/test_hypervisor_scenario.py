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

"""Spike: ops.testing (state-transition) tests for openstack-hypervisor.

Findings
--------
1. **Machine charm / no containers** – openstack-hypervisor is a machine charm
   using snaps, not Pebble.  No containers are needed in the State.
2. **Heavy mocking required** – the charm imports snap, socket, subprocess,
   epa_client, ConsulNotifyRequirer, and COSAgentProvider.  All must be
   patched before the Context instantiates the charm.  ``monkeypatch`` in an
   autouse conftest fixture handles this cleanly.
3. **Mandatory relations** – amqp, identity-credentials, ovsdb-cms, and
   nova-service are non-optional requires endpoints.  All four must be
   present (with valid data) for the charm to proceed past
   ``check_relation_handlers_ready``.
4. **configure_unit** – the main work method calls ``ensure_snap_present()``,
   ``get_local_ip_by_default_route()``, ``contexts()``, and
   ``set_snap_data()``.  Since these hit real OS APIs, the snap mock and
   ip mock in conftest cover them.
5. **Limitations** – full configure_unit success tests are difficult because
   the context objects (``contexts.certificates``, ``contexts.ovsdb_cms``,
   etc.) require deeply populated relation data.  The tests below cover the
   relation-readiness layer; deeper snap-configuration tests remain in the
   harness suite.
"""

from pathlib import (
    Path,
)

import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    amqp_relation_complete,
    assert_relation_broken_causes_blocked_or_waiting,
    identity_credentials_relation_complete,
    identity_credentials_secret,
    mandatory_relations_from_charmcraft,
)

CHARM_ROOT = Path(__file__).parents[2]
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)

# ---------------------------------------------------------------------------
# Relation / secret builders for hypervisor-specific endpoints
# ---------------------------------------------------------------------------

OVSDB_CMS_APP_DATA = {
    "loadbalancer-address": "10.15.24.37",
    "sb-connection-string": "ssl:10.15.24.37:6642",
}
OVSDB_CMS_UNIT_DATA = {
    "bound-address": "10.1.176.143",
    "bound-hostname": "ovn-relay-0.ovn-relay-endpoints.openstack.svc.cluster.local",
    "egress-subnets": "10.20.21.10/32",
    "ingress-address": "10.20.21.10",
}


def ovsdb_cms_relation() -> testing.Relation:
    """ovsdb-cms relation with loadbalancer address."""
    return testing.Relation(
        endpoint="ovsdb-cms",
        remote_app_name="ovn-relay",
        remote_app_data=OVSDB_CMS_APP_DATA,
        remote_units_data={0: OVSDB_CMS_UNIT_DATA},
    )


def nova_service_relation() -> testing.Relation:
    """nova-service relation with spice-proxy-url."""
    return testing.Relation(
        endpoint="nova-service",
        remote_app_name="nova",
        remote_app_data={
            "spice-proxy-url": "http://INGRESS_IP/nova-spiceproxy/spice_auto.html",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.50"}},
    )


def _all_mandatory_relations() -> list:
    return [
        amqp_relation_complete(),
        identity_credentials_relation_complete(),
        ovsdb_cms_relation(),
        nova_service_relation(),
    ]


def _all_secrets() -> list:
    return [identity_credentials_secret()]


# ---------------------------------------------------------------------------
# Tests: blocked when no relations
# ---------------------------------------------------------------------------


class TestBlockedWhenNoRelations:
    """Config-changed with missing mandatory relations → blocked/waiting."""

    def test_blocked_when_no_relations(self, ctx):
        """Charm should be blocked/waiting when no relations are present."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")

    def test_blocked_with_only_one_relation(self, ctx):
        """With only amqp present, should still block on missing relations."""
        state_in = testing.State(
            leader=True,
            relations=[amqp_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting")


# ---------------------------------------------------------------------------
# Tests: blocked when each mandatory relation missing (parametrized)
# ---------------------------------------------------------------------------


def _relation_builder(name: str):
    """Return the complete relation builder for a mandatory endpoint."""
    builders = {
        "amqp": amqp_relation_complete,
        "identity-credentials": identity_credentials_relation_complete,
        "ovsdb-cms": ovsdb_cms_relation,
        "nova-service": nova_service_relation,
    }
    return builders[name]


@pytest.mark.parametrize("missing_rel", sorted(MANDATORY_RELATIONS))
class TestBlockedWhenEachRelationMissing:
    """When one mandatory relation is absent, charm must not be active."""

    def test_blocked_when_relation_missing(self, ctx, missing_rel):
        """Test blocked when relation missing."""
        remaining = [
            r for r in _all_mandatory_relations() if r.endpoint != missing_rel
        ]
        secrets = _all_secrets()
        state_in = testing.State(
            leader=True,
            relations=remaining,
            secrets=secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status.name in ("blocked", "waiting"), (
            f"Expected blocked/waiting when '{missing_rel}' missing, "
            f"got {state_out.unit_status}"
        )


# ---------------------------------------------------------------------------
# Tests: all relations complete
# ---------------------------------------------------------------------------


class TestAllRelationsComplete:
    """Config-changed with all mandatory relations → bootstrap progresses."""

    def test_all_relations_present(self, ctx, complete_state):
        """With all mandatory relations, charm should proceed past relation checks.

        Note: the charm may still end up in waiting/maintenance because
        configure_unit performs additional work (snap installation,
        certificate retrieval, etc.) that depends on deeper relation data.
        The key assertion is that it does NOT block on missing relations.
        """
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        # Should not be blocked on missing integration
        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite all "
                f"mandatory relations present: {status.message}"
            )


# ---------------------------------------------------------------------------
# Tests: non-leader waiting
# ---------------------------------------------------------------------------


class TestWaitingNonLeader:
    """Non-leader with all relations should wait for leader readiness."""

    def test_waiting_non_leader(
        self, ctx, complete_relations, complete_secrets
    ):
        """Non-leader unit waits — either for leader or data."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            secrets=complete_secrets,
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Non-leader should not reach active — it either waits for the
        # leader to be ready, or encounters data-missing errors because
        # the full configure_unit path requires certificates etc.
        assert state_out.unit_status.name in (
            "waiting",
            "blocked",
            "maintenance",
        )


# ---------------------------------------------------------------------------
# Tests: install event triggers snap installation
# ---------------------------------------------------------------------------


class TestInstallEvent:
    """Install event should trigger snap installation logic."""

    def test_install_event_runs(self, ctx):
        """Install event should not crash with mocked externals."""
        state_in = testing.State(leader=True)
        # Should not raise — snap installation is mocked
        state_out = ctx.run(ctx.on.install(), state_in)
        # Status may be maintenance/blocked, but should not error
        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
        )


# ---------------------------------------------------------------------------
# Tests: config-changed with debug flag
# ---------------------------------------------------------------------------


class TestConfigChanged:
    """Config changes should be handled without errors."""

    def test_config_changed_with_debug(
        self, ctx, complete_relations, complete_secrets
    ):
        """Changing debug config should not crash."""
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            secrets=complete_secrets,
            config={"debug": True},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        # Should not error — any status is acceptable
        assert state_out.unit_status.name in (
            "blocked",
            "waiting",
            "maintenance",
            "active",
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
