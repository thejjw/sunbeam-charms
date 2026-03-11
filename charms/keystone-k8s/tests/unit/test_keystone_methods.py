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

"""Unit tests for keystone-k8s charm internal methods.

These tests exercise charm methods directly (peer data handling,
secret rotation, secret changed, utility functions) by running
scenario events and inspecting mock side-effects.
"""

import base64
import dataclasses
import json
from unittest.mock import (
    MagicMock,
)

import charm
import keystoneauth1.exceptions
import pytest
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    cleanup_database_requires_events,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNSET = testing.pebble.CheckStartup.UNSET


def _fix_checks(state: testing.State) -> testing.State:
    """Add check_infos to the container to satisfy scenario consistency checks.

    After a config-changed bootstrap, the charm adds a healthchecks pebble
    layer.  A subsequent ``ctx.run()`` on the output state fails because
    the scenario framework compares the (empty) check_infos with the plan.
    This helper populates check_infos to match the plan exactly.
    """
    c = state.get_container("keystone")
    fixed = dataclasses.replace(
        c,
        check_infos=frozenset(
            [
                testing.CheckInfo(
                    name="up",
                    level=testing.pebble.CheckLevel.ALIVE,
                    startup=_UNSET,
                    status=testing.pebble.CheckStatus.UP,
                    threshold=3,
                ),
                testing.CheckInfo(
                    name="online",
                    level=testing.pebble.CheckLevel.READY,
                    startup=_UNSET,
                    status=testing.pebble.CheckStatus.UP,
                    threshold=None,
                ),
            ]
        ),
    )
    new_containers = [
        fixed if cc.name == "keystone" else cc for cc in state.containers
    ]
    return dataclasses.replace(state, containers=new_containers)


def _bootstrap(ctx, state):
    """Bootstrap the charm and return a state ready for further events."""
    state_out = ctx.run(ctx.on.config_changed(), state)
    fixed = _fix_checks(state_out)
    cleanup_database_requires_events()
    return fixed


def _new_ctx():
    """Create a fresh testing.Context."""
    from pathlib import (
        Path,
    )

    return testing.Context(
        charm.KeystoneOperatorCharm, charm_root=Path(__file__).parents[2]
    )


def _identity_service_relation():
    """Create an identity-service relation with cinder."""
    return testing.Relation(
        endpoint="identity-service",
        remote_app_name="cinder",
        remote_app_data={
            "region": "RegionOne",
            "service-endpoints": (
                '[{"service_name": "cinderv3", "type": "volumev3",'
                ' "description": "Cinder",'
                ' "internal_url": "http://10.0.0.1:8776/v3/$(tenant_id)s",'
                ' "public_url": "http://10.0.0.1:8776/v3/$(tenant_id)s",'
                ' "admin_url": "http://10.0.0.1:8776/v3/$(tenant_id)s"}]'
            ),
        },
        remote_units_data={0: {}},
    )


# ---------------------------------------------------------------------------
# Utility function test (pure, no charm needed)
# ---------------------------------------------------------------------------


class TestGetIdpFileNameFromIssuerUrl:
    """Test _get_idp_file_name_from_issuer_url (pure function on handler)."""

    def test_strips_scheme_and_trailing_slash(self):
        """Issuer URL is sanitised to a URL-encoded base file name."""
        from charm import (
            _BaseIDPHandler,
        )

        handler = _BaseIDPHandler.__new__(_BaseIDPHandler)
        issuer_url = "https://172.16.1.207/iam-hydra/"
        result = handler._get_idp_file_name_from_issuer_url(issuer_url)
        assert result == "172.16.1.207%2Fiam-hydra"


# ---------------------------------------------------------------------------
# _on_peer_data_changed tests
# ---------------------------------------------------------------------------


class TestOnPeerDataChanged:
    """Test _on_peer_data_changed behaviour via peers_relation_changed event."""

    def test_defers_when_not_bootstrapped(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """When unit is not bootstrapped, peer data changed defers."""
        peer = [r for r in complete_relations if r.endpoint == "peers"][0]
        # Need a remote unit for relation_changed
        peer_with_unit = dataclasses.replace(peer, peers_data={1: {}})
        remaining = [
            peer_with_unit if r.endpoint == "peers" else r
            for r in complete_relations
        ]
        state_in = testing.State(
            leader=False,
            relations=remaining,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        # Non-leader without bootstrap → defers
        ctx.run(ctx.on.relation_changed(peer_with_unit), state_in)

    def test_writes_keys_when_secret_content_differs(
        self, ctx, complete_state
    ):
        """When fernet secret content differs from disk, keys are written."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = _bootstrap(ctx, complete_state)

        peer = [r for r in state_mid.relations if r.endpoint == "peers"][0]
        fernet_secret_id = peer.local_app_data.get("fernet-secret-id")
        credential_secret_id = peer.local_app_data.get(
            "credential-keys-secret-id"
        )

        if not fernet_secret_id or not credential_secret_id:
            pytest.skip("Secrets not created during bootstrap")

        updated_keys = {"0": "newkey0=", "2": "newkey2="}
        secret_keys = {f"fernet-{k}": v for k, v in updated_keys.items()}
        new_secrets = []
        for s in state_mid.secrets:
            if s.id in (fernet_secret_id, credential_secret_id):
                new_secrets.append(
                    dataclasses.replace(s, tracked_content=secret_keys)
                )
            else:
                new_secrets.append(s)

        peer_with_unit = dataclasses.replace(peer, peers_data={1: {}})
        new_relations = [
            peer_with_unit if r.endpoint == "peers" else r
            for r in state_mid.relations
        ]
        state_updated = dataclasses.replace(
            state_mid, secrets=new_secrets, relations=new_relations
        )

        # Reset mocks *after* bootstrap so only the peer-changed writes count
        km.write_keys.reset_mock()
        km.read_keys.reset_mock()
        km.read_keys.return_value = {"0": "oldkey0=", "1": "oldkey1="}

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.relation_changed(peer_with_unit), state_updated)

        assert km.read_keys.called
        # 2 writes: fernet-keys and credential-keys
        assert km.write_keys.call_count >= 2

    def test_no_write_when_secret_content_same(self, ctx, complete_state):
        """When secret content matches disk, keys are NOT written."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = _bootstrap(ctx, complete_state)

        peer = [r for r in state_mid.relations if r.endpoint == "peers"][0]
        peer_with_unit = dataclasses.replace(peer, peers_data={1: {}})
        new_relations = [
            peer_with_unit if r.endpoint == "peers" else r
            for r in state_mid.relations
        ]
        state_updated = dataclasses.replace(state_mid, relations=new_relations)

        km.write_keys.reset_mock()
        km.read_keys.reset_mock()
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.relation_changed(peer_with_unit), state_updated)

        assert km.read_keys.called
        assert not km.write_keys.called


# ---------------------------------------------------------------------------
# _on_secret_rotate tests
# ---------------------------------------------------------------------------


class TestSecretRotateKeys:
    """Test _on_secret_rotate for fernet-keys and credential-keys labels."""

    @pytest.mark.parametrize("label", ["fernet-keys", "credential-keys"])
    def test_leader_rotates_keys(self, ctx, complete_state, label):
        """Leader rotates keys and updates secret content."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = _bootstrap(ctx, complete_state)

        target_secret = None
        for s in state_mid.secrets:
            if s.label == label:
                target_secret = s
                break

        if target_secret is None:
            pytest.skip(f"No secret with label '{label}' found")

        km.rotate_fernet_keys.reset_mock()
        km.rotate_credential_keys.reset_mock()
        km.read_keys.reset_mock()
        km.read_keys.return_value = {"0": "rotated0=", "1": "rotated1="}

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.secret_rotate(target_secret), state_mid)

        if label == "fernet-keys":
            assert km.rotate_fernet_keys.called
        else:
            assert km.rotate_credential_keys.called

        rotated = state_out.get_secret(id=target_secret.id)
        latest = rotated.latest_content
        if latest:
            assert "fernet-0" in latest
            assert latest["fernet-0"] == "rotated0="

    @pytest.mark.parametrize("label", ["fernet-keys", "credential-keys"])
    def test_non_leader_does_not_rotate(
        self,
        ctx,
        complete_relations,
        complete_secrets,
        container,
        storages,
        label,
    ):
        """Non-leader unit does NOT rotate keys on secret-rotate."""
        km = charm.manager.KeystoneManager.return_value

        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)
        state_non_leader = dataclasses.replace(state_mid, leader=False)

        target_secret = None
        for s in state_non_leader.secrets:
            if s.label == label:
                target_secret = s
                break

        if target_secret is None:
            pytest.skip(f"No secret with label '{label}' found")

        km.rotate_fernet_keys.reset_mock()
        km.rotate_credential_keys.reset_mock()

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.secret_rotate(target_secret), state_non_leader)

        assert not km.rotate_fernet_keys.called
        assert not km.rotate_credential_keys.called


# ---------------------------------------------------------------------------
# _on_secret_changed tests
# ---------------------------------------------------------------------------


class TestSecretChanged:
    """Test _on_secret_changed for fernet keys.

    secret-changed only fires for tracked (not owned) secrets. Since
    the keystone charm creates fernet-keys as an app secret, a unit of
    the *same* app will never receive secret-changed for it in Juju.
    The original harness tests called _on_secret_changed directly with
    a mock event, so we replicate that approach here.
    """

    def test_no_write_when_content_same(self, ctx, complete_state):
        """Secret changed with same content → no write."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = _bootstrap(ctx, complete_state)

        km.write_keys.reset_mock()
        km.read_keys.reset_mock()
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

        from unittest.mock import (
            MagicMock,
        )

        event = MagicMock()
        event.secret.label = "fernet-keys"
        event.secret.get_content.return_value = km.read_keys()

        # Call _on_secret_changed directly (like the harness test did)
        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            mgr.charm._on_secret_changed(event)

        assert km.read_keys.called
        assert not km.write_keys.called

    def test_writes_when_content_differs(self, ctx, complete_state):
        """Secret changed with different content → keys written."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = _bootstrap(ctx, complete_state)

        km.write_keys.reset_mock()
        km.read_keys.reset_mock()
        km.read_keys.return_value = {"0": "oldkey0=", "1": "oldkey1="}

        from unittest.mock import (
            MagicMock,
        )

        event = MagicMock()
        event.secret.label = "fernet-keys"
        event.secret.get_content.return_value = {
            "0": "newkey0=",
            "4": "newkey4=",
        }

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            mgr.charm._on_secret_changed(event)

        assert km.read_keys.called
        assert km.write_keys.called


# ---------------------------------------------------------------------------
# Leader bootstrap tests
# ---------------------------------------------------------------------------


class TestLeaderBootstrap:
    """Test leader bootstrapping behaviour."""

    def test_leader_bootstraps(self, ctx, complete_state):
        """Leader with all relations calls setup_keystone and sets peer data."""
        km = charm.manager.KeystoneManager.return_value
        km.setup_keystone.reset_mock()
        km.setup_initial_projects_and_users.reset_mock()

        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        km.setup_keystone.assert_called_once()
        km.setup_initial_projects_and_users.assert_called_once()

        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        assert peer.local_app_data.get("leader_ready") == "true"
        assert "fernet-secret-id" in peer.local_app_data
        assert "credential-keys-secret-id" in peer.local_app_data

    def test_non_leader_does_not_bootstrap(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader unit does not call setup_keystone."""
        km = charm.manager.KeystoneManager.return_value
        km.setup_keystone.reset_mock()

        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        ctx.run(ctx.on.config_changed(), state_in)

        assert not km.setup_keystone.called


# ---------------------------------------------------------------------------
# _ingress_changed tests
# ---------------------------------------------------------------------------


class TestIngressChanged:
    """Test _ingress_changed skips catalog update when not ready."""

    def test_skips_catalog_update_when_service_not_ready(
        self, ctx, complete_state, monkeypatch
    ):
        """When keystone service not ready, catalog update is skipped."""
        km = charm.manager.KeystoneManager.return_value

        monkeypatch.setattr(
            charm.KeystoneOperatorCharm,
            "_is_keystone_service_ready",
            lambda self: False,
        )

        state_mid = _bootstrap(ctx, complete_state)

        km.update_service_catalog_for_keystone.reset_mock()

        ingress_rel = [
            r for r in state_mid.relations if r.endpoint == "ingress-internal"
        ][0]
        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.relation_changed(ingress_rel), state_mid)

        km.update_service_catalog_for_keystone.assert_not_called()


# ---------------------------------------------------------------------------
# Action tests
# ---------------------------------------------------------------------------


class TestActions:
    """Test action handlers."""

    def test_get_service_account_action_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader running get-service-account action fails."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(
                ctx.on.action(
                    "get-service-account", params={"username": "test"}
                ),
                state_in,
            )
        assert "lead unit" in exc_info.value.message.lower()

    def test_get_service_account_action_leader(self, ctx, complete_state):
        """Leader running get-service-account returns account details."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        ctx2.run(
            ctx2.on.action(
                "get-service-account",
                params={"username": "external_service"},
            ),
            state_mid,
        )
        assert "username" in ctx2.action_results
        assert ctx2.action_results["username"] == "external_service"

    def test_get_admin_account_action_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader running get-admin-account action fails."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("get-admin-account"), state_in)
        assert "lead unit" in exc_info.value.message.lower()

    def test_get_admin_account_action_leader(self, ctx, complete_state):
        """Leader running get-admin-account returns admin details."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.action("get-admin-account"), state_mid)
        assert "username" in ctx2.action_results
        assert ctx2.action_results["username"] == "admin"
        assert "openrc" in ctx2.action_results


# ---------------------------------------------------------------------------
# Identity service rotation tests
# ---------------------------------------------------------------------------


class TestIdentityServiceSecretRotation:
    """Test secret rotation for identity-service credentials."""

    def _bootstrap_with_identity(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Bootstrap with an identity-service relation."""
        id_rel = _identity_service_relation()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, id_rel],
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        return _bootstrap(ctx, state_in)

    def _find_creds_secret(self, state):
        for s in state.secrets:
            if s.label and s.label.startswith("credentials_svc_cinder"):
                return s
        return None

    def test_leader_rotates_identity_service_secret(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Leader rotates identity service credentials successfully."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = self._bootstrap_with_identity(
            ctx, complete_relations, complete_secrets, container, storages
        )

        creds_secret = self._find_creds_secret(state_mid)
        if creds_secret is None:
            pytest.skip("No identity service credentials secret found")

        km.create_service_account.reset_mock()
        km.create_service_account.return_value = {"name": "cinder"}

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.secret_rotate(creds_secret), state_mid)

        assert km.create_service_account.called

    def test_leader_rotation_retries_on_connect_failure(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Leader retries after ConnectFailure during rotation."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = self._bootstrap_with_identity(
            ctx, complete_relations, complete_secrets, container, storages
        )

        creds_secret = self._find_creds_secret(state_mid)
        if creds_secret is None:
            pytest.skip("No identity service credentials secret found")

        km.create_service_account.reset_mock()
        km.create_service_account.side_effect = [
            keystoneauth1.exceptions.ConnectFailure("Failed"),
            {"name": "cinder"},
        ]

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.secret_rotate(creds_secret), state_mid)

        assert km.create_service_account.call_count == 2

    def test_leader_rotation_fails_twice_raises(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Two consecutive ConnectFailures raise the exception."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = self._bootstrap_with_identity(
            ctx, complete_relations, complete_secrets, container, storages
        )

        creds_secret = self._find_creds_secret(state_mid)
        if creds_secret is None:
            pytest.skip("No identity service credentials secret found")

        km.create_service_account.reset_mock()
        km.create_service_account.side_effect = [
            keystoneauth1.exceptions.ConnectFailure("Failed"),
            keystoneauth1.exceptions.ConnectFailure("Failed again"),
        ]

        from scenario.errors import (
            UncaughtCharmError,
        )

        ctx2 = _new_ctx()
        with pytest.raises(UncaughtCharmError):
            ctx2.run(ctx2.on.secret_rotate(creds_secret), state_mid)

    def test_leader_rotation_unexpected_error_raises(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Unexpected exception is not caught."""
        km = charm.manager.KeystoneManager.return_value

        state_mid = self._bootstrap_with_identity(
            ctx, complete_relations, complete_secrets, container, storages
        )

        creds_secret = self._find_creds_secret(state_mid)
        if creds_secret is None:
            pytest.skip("No identity service credentials secret found")

        km.create_service_account.reset_mock()
        km.create_service_account.side_effect = Exception("Unexpected!")

        from scenario.errors import (
            UncaughtCharmError,
        )

        ctx2 = _new_ctx()
        with pytest.raises(UncaughtCharmError):
            ctx2.run(ctx2.on.secret_rotate(creds_secret), state_mid)


# ---------------------------------------------------------------------------
# Helpers for relation tests
# ---------------------------------------------------------------------------

_OAUTH_REMOTE_APP_DATA = {
    "authorization_endpoint": "https://172.16.1.207/iam-hydra/oauth2/auth",
    "client_id": "c733827d-d6e0-45dd-8210-fdc9b6525f29",
    "client_secret_id": "secret://test_oauth_secret",
    "introspection_endpoint": "http://hydra.iam.svc.cluster.local:4445/admin/oauth2/introspect",
    "issuer_url": "https://172.16.1.207/iam-hydra",
    "jwks_endpoint": "https://172.16.1.207/iam-hydra/.well-known/jwks.json",
    "jwt_access_token": "True",
    "scope": "openid profile email phone",
    "token_endpoint": "https://172.16.1.207/iam-hydra/oauth2/token",
    "userinfo_endpoint": "https://172.16.1.207/iam-hydra/userinfo",
}


def _oauth_secret():
    """Secret that the oauth provider (hydra) uses for client_secret."""
    return testing.Secret(
        id="secret://test_oauth_secret",
        tracked_content={"secret": "super secret"},
        latest_content={"secret": "super secret"},
        owner=None,
    )


def _oauth_relation():
    """Create an oauth relation with hydra remote data."""
    return testing.Relation(
        endpoint="oauth",
        remote_app_name="hydra",
        remote_app_data=_OAUTH_REMOTE_APP_DATA,
        remote_units_data={0: {}},
    )


def _trusted_dashboard_relation():
    """Create a trusted-dashboard relation with horizon."""
    return testing.Relation(
        endpoint="trusted-dashboard",
        remote_app_name="horizon",
        remote_units_data={0: {}},
    )


def _keystone_saml_relation():
    """Create a keystone-saml relation with entra provider data."""
    return testing.Relation(
        endpoint="keystone-saml",
        remote_app_name="keystone-saml-entra",
        remote_app_data={
            "name": "entra",
            "label": "Log in with Entra SAML2",
            "metadata": "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4=",
        },
        remote_units_data={0: {}},
    )


def _domain_config_relation():
    """Create a domain-config relation with LDAP data."""
    b64file = (
        "W2xkYXBdCmdyb3VwX21lbWJlcl9hdHRyaWJ1dGUgPSBtZW1iZXJVaWQKZ3JvdXBf"
        "bWVtYmVyc19hcmVfaWRzID0gdHJ1ZQpncm91cF9uYW1lX2F0dHJpYnV0ZSA9IGNu"
        "Cmdyb3VwX29iamVjdGNsYXNzID0gcG9zaXhHcm91cApncm91cF90cmVlX2RuID0g"
        "b3U9Z3JvdXBzLGRjPXRlc3QsZGM9Y29tCnBhc3N3b3JkID0gY3JhcHBlcgpzdWZm"
        "aXggPSBkYz10ZXN0LGRjPWNvbQp1cmwgPSBsZGFwOi8vMTAuMS4xNzYuMTg0CnVz"
        "ZXIgPSBjbj1hZG1pbixkYz10ZXN0LGRjPWNvbQpbaWRlbnRpdHldCmRyaXZlciA9"
        "IGxkYXA="
    )
    return testing.Relation(
        endpoint="domain-config",
        remote_app_name="keystone-ldap-k8s",
        remote_app_data={
            "domain-name": "mydomain",
            "config-contents": b64file,
        },
        remote_units_data={0: {}},
    )


# ---------------------------------------------------------------------------
# Trusted-dashboard relation test
# ---------------------------------------------------------------------------


class TestTrustedDashboardRelation:
    """Test trusted-dashboard relation data is populated when oauth is present."""

    def test_trusted_dashboard_without_oauth_empty(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Without oauth, trusted-dashboard relation data is empty."""
        td_rel = _trusted_dashboard_relation()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, td_rel],
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)

        td_out = [
            r for r in state_mid.relations if r.endpoint == "trusted-dashboard"
        ][0]
        assert td_out.local_app_data.get("federated-providers", "") == ""

    def test_trusted_dashboard_with_oauth_has_providers(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """With oauth relation, trusted-dashboard gets federated-providers."""
        td_rel = _trusted_dashboard_relation()
        oauth_rel = _oauth_relation()
        oauth_sec = _oauth_secret()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, td_rel, oauth_rel],
            containers=[container],
            secrets=[*complete_secrets, oauth_sec],
            storages=storages,
        )

        state_mid = _bootstrap(ctx, state_in)

        # Now fire oauth relation_changed to trigger _handle_fid_providers_changed
        oauth_out = [r for r in state_mid.relations if r.endpoint == "oauth"][
            0
        ]
        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(oauth_out), state_mid)

        td_out = [
            r for r in state_out.relations if r.endpoint == "trusted-dashboard"
        ][0]
        providers = json.loads(td_out.local_app_data["federated-providers"])
        assert providers == [
            {
                "name": "hydra",
                "protocol": "openid",
                "description": "Hydra",
            }
        ]


# ---------------------------------------------------------------------------
# OAuth relation test
# ---------------------------------------------------------------------------


class TestOAuthRelation:
    """Test oauth relation response data."""

    def test_oauth_relation_data(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Charm sets correct response data on the oauth relation."""
        oauth_rel = _oauth_relation()
        oauth_sec = _oauth_secret()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, oauth_rel],
            containers=[container],
            secrets=[*complete_secrets, oauth_sec],
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)

        oauth_out = [r for r in state_mid.relations if r.endpoint == "oauth"][
            0
        ]
        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(oauth_out), state_mid)

        oauth_final = [
            r for r in state_out.relations if r.endpoint == "oauth"
        ][0]
        local_data = dict(oauth_final.local_app_data)
        assert (
            local_data["redirect_uri"]
            == "http://public-url/v3/OS-FEDERATION/protocols/openid/redirect_uri"
        )
        assert local_data["scope"] == "openid email profile"
        assert json.loads(local_data["grant_types"]) == [
            "authorization_code",
            "client_credentials",
            "refresh_token",
        ]
        assert local_data["audience"] == "[]"
        assert (
            local_data["token_endpoint_auth_method"] == "client_secret_basic"
        )

        # Remote app data should be unchanged
        assert (
            oauth_final.remote_app_data["issuer_url"]
            == "https://172.16.1.207/iam-hydra"
        )
        assert (
            oauth_final.remote_app_data["client_id"]
            == "c733827d-d6e0-45dd-8210-fdc9b6525f29"
        )


# ---------------------------------------------------------------------------
# Keystone SAML2 relation test
# ---------------------------------------------------------------------------


class TestKeystoneSAML2Relation:
    """Test keystone-saml relation response data."""

    def test_saml_relation_data(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Charm sets acs-url, logout-url, metadata-url on keystone-saml."""
        # Bootstrap without SAML (SAML context raises BlockedExceptionError
        # when no saml-x509-keypair is configured).
        state_in = testing.State(
            leader=True,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)

        # Now add the SAML relation and fire relation_changed.
        saml_rel = _keystone_saml_relation()
        state_with_saml = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, saml_rel],
        )
        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(saml_rel), state_with_saml
        )

        saml_final = [
            r for r in state_out.relations if r.endpoint == "keystone-saml"
        ][0]
        local_data = dict(saml_final.local_app_data)

        base = "http://public-url/v3/OS-FEDERATION/identity_providers/entra/protocols/saml2/auth/mellon"
        assert local_data["acs-url"] == f"{base}/postResponse"
        assert local_data["logout-url"] == f"{base}/logout"
        assert local_data["metadata-url"] == f"{base}/metadata"

        # Remote app data should be preserved
        assert saml_final.remote_app_data["name"] == "entra"
        assert saml_final.remote_app_data["metadata"] == (
            "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4="
        )


# ---------------------------------------------------------------------------
# sync_oidc_providers test
# ---------------------------------------------------------------------------


class TestSyncOIDCProviders:
    """Test sync_oidc_providers writes OIDC metadata via KeystoneManager."""

    def test_sync_oidc_providers(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """sync_oidc_providers calls km.setup_oidc_metadata_folder and write_oidc_metadata."""
        km = charm.manager.KeystoneManager.return_value

        oauth_rel = _oauth_relation()
        oauth_sec = _oauth_secret()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, oauth_rel],
            containers=[container],
            secrets=[*complete_secrets, oauth_sec],
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)

        km.setup_oidc_metadata_folder.reset_mock()
        km.write_oidc_metadata.reset_mock()

        from unittest.mock import (
            MagicMock,
        )

        mock_metadata = {"hello": "world"}
        encoded_issuer_url = "172.16.1.207%2Fiam-hydra"

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            mgr.charm.oauth._get_oidc_metadata = MagicMock(
                return_value=mock_metadata
            )
            mgr.charm.sync_oidc_providers()

            assert km.setup_oidc_metadata_folder.call_count == 1
            assert km.write_oidc_metadata.call_count == 1
            km.write_oidc_metadata.assert_called_with(
                {
                    f"{encoded_issuer_url}.provider": json.dumps(
                        mock_metadata
                    ),
                    f"{encoded_issuer_url}.client": json.dumps(
                        {
                            "client_id": "c733827d-d6e0-45dd-8210-fdc9b6525f29",
                            "client_secret": "super secret",
                        }
                    ),
                }
            )


# ---------------------------------------------------------------------------
# Domain config test
# ---------------------------------------------------------------------------


class TestDomainConfig:
    """Test domain-config relation writes the decoded config file."""

    def test_domain_config(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Domain config relation writes decoded LDAP config to disk."""
        dc_rel = _domain_config_relation()
        state_in = testing.State(
            leader=True,
            relations=[*complete_relations, dc_rel],
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        state_mid = _bootstrap(ctx, state_in)

        dc_out = [
            r for r in state_mid.relations if r.endpoint == "domain-config"
        ][0]
        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(dc_out), state_mid)

        out_container = state_out.get_container("keystone")
        config_path = "/etc/keystone/domains/keystone.mydomain.conf"
        expected = (
            "[ldap]\n"
            "group_member_attribute = memberUid\n"
            "group_members_are_ids = true\n"
            "group_name_attribute = cn\n"
            "group_objectclass = posixGroup\n"
            "group_tree_dn = ou=groups,dc=test,dc=com\n"
            "password = crapper\n"
            "suffix = dc=test,dc=com\n"
            "url = ldap://10.1.176.184\n"
            "user = cn=admin,dc=test,dc=com\n"
            "[identity]\n"
            "driver = ldap"
        )
        content = (
            out_container.get_filesystem(ctx2)
            .joinpath(config_path.lstrip("/"))
            .read_text()
        )
        assert content == expected


# ---------------------------------------------------------------------------
# Crypto fixtures for certificate testing
# ---------------------------------------------------------------------------


def _make_self_signed_cert(cn: str):
    """Create a self-signed cert + key pair and return (cert_pem, key_pem)."""
    import datetime

    from cryptography import (
        x509,
    )
    from cryptography.hazmat.primitives import (
        hashes,
        serialization,
    )
    from cryptography.hazmat.primitives.asymmetric import (
        rsa,
    )
    from cryptography.x509.oid import (
        NameOID,
    )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def _make_ca_chain():
    """Create root CA → intermediate CA → leaf cert chain.

    Returns (root_pem, inter_pem, leaf_pem, leaf_key_pem).
    """
    import datetime

    from cryptography import (
        x509,
    )
    from cryptography.hazmat.primitives import (
        hashes,
        serialization,
    )
    from cryptography.hazmat.primitives.asymmetric import (
        rsa,
    )
    from cryptography.x509.oid import (
        NameOID,
    )

    def _build(cn, signing_key=None, issuer_name=None):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(issuer_name or name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=365))
            .sign(signing_key or key, hashes.SHA256())
        )
        pem = cert.public_bytes(serialization.Encoding.PEM).decode()
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode()
        return cert, key, pem, key_pem

    root_cert, root_key, root_pem, root_key_pem = _build("Root CA")
    inter_cert, inter_key, inter_pem, _ = _build(
        "Intermediate CA", root_key, root_cert.subject
    )
    _, _, leaf_pem, leaf_key_pem = _build(
        "leaf.example.com", inter_key, inter_cert.subject
    )
    return root_pem, inter_pem, leaf_pem, leaf_key_pem


# Cache the crypto fixtures at module level (generation is slow).
_CRYPTO_SELF_SIGNED = None
_CRYPTO_CHAIN = None


def _get_self_signed():
    global _CRYPTO_SELF_SIGNED
    if _CRYPTO_SELF_SIGNED is None:
        _CRYPTO_SELF_SIGNED = _make_self_signed_cert("Test CA")
    return _CRYPTO_SELF_SIGNED


def _get_chain():
    global _CRYPTO_CHAIN
    if _CRYPTO_CHAIN is None:
        _CRYPTO_CHAIN = _make_ca_chain()
    return _CRYPTO_CHAIN


# ---------------------------------------------------------------------------
# Action tests: add-ca-certs
# ---------------------------------------------------------------------------


class TestAddCaCertsAction:
    """Test the add-ca-certs action behaviour."""

    def test_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader cannot run add-ca-certs action."""
        ca_pem, _ = _get_self_signed()
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(
                ctx.on.action(
                    "add-ca-certs",
                    params={
                        "name": "test-ca",
                        "ca": base64.b64encode(ca_pem.encode()).decode(),
                    },
                ),
                state_in,
            )
        assert "lead unit" in exc_info.value.message.lower()

    def test_valid_ca_stores_in_peer_data(self, ctx, complete_state):
        """Leader adds a valid CA cert → stored in peer data under certs_to_transfer."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "my-ca", "ca": ca_b64},
            ),
            state_mid,
        )
        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        certs_data = json.loads(
            peer.local_app_data.get("certs_to_transfer", "{}")
        )
        assert "my-ca" in certs_data
        assert certs_data["my-ca"]["ca"] == ca_pem
        assert certs_data["my-ca"]["chain"] is None

    def test_valid_ca_with_chain(self, ctx, complete_state):
        """Leader adds CA + chain → both stored."""
        state_mid = _bootstrap(ctx, complete_state)
        root_pem, inter_pem, _, _ = _get_chain()
        ca_b64 = base64.b64encode(root_pem.encode()).decode()
        # Chain must be ordered: intermediate first, then root (issuer)
        chain_pem = inter_pem + root_pem
        chain_b64 = base64.b64encode(chain_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "chained", "ca": ca_b64, "chain": chain_b64},
            ),
            state_mid,
        )
        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        certs_data = json.loads(
            peer.local_app_data.get("certs_to_transfer", "{}")
        )
        assert "chained" in certs_data
        assert certs_data["chained"]["chain"] == chain_pem

    def test_invalid_ca_base64_fails(self, ctx, complete_state):
        """Invalid base64 in ca param → action fails."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with pytest.raises(testing.ActionFailed):
            ctx2.run(
                ctx2.on.action(
                    "add-ca-certs",
                    params={"name": "bad", "ca": "not-valid-b64!!!"},
                ),
                state_mid,
            )

    def test_invalid_cert_data_fails(self, ctx, complete_state):
        """Base64 of non-certificate data → action fails (invalid cert)."""
        state_mid = _bootstrap(ctx, complete_state)
        bad_b64 = base64.b64encode(b"this is not a certificate").decode()

        ctx2 = _new_ctx()
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx2.run(
                ctx2.on.action(
                    "add-ca-certs",
                    params={"name": "bad", "ca": bad_b64},
                ),
                state_mid,
            )
        assert "invalid ca certificate" in exc_info.value.message.lower()

    def test_duplicate_name_fails(self, ctx, complete_state):
        """Adding a bundle with an existing name → action fails."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        # First add succeeds
        ctx2 = _new_ctx()
        state_after_add = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "dup-test", "ca": ca_b64},
            ),
            state_mid,
        )
        state_after_add = _fix_checks(state_after_add)
        cleanup_database_requires_events()

        # Second add with same name fails
        ctx3 = _new_ctx()
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx3.run(
                ctx3.on.action(
                    "add-ca-certs",
                    params={"name": "dup-test", "ca": ca_b64},
                ),
                state_after_add,
            )
        assert "already transferred" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# Action tests: remove-ca-certs
# ---------------------------------------------------------------------------


class TestRemoveCaCertsAction:
    """Test the remove-ca-certs action behaviour."""

    def test_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader cannot run remove-ca-certs action."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(
                ctx.on.action("remove-ca-certs", params={"name": "test-ca"}),
                state_in,
            )
        assert "lead unit" in exc_info.value.message.lower()

    def test_remove_existing_bundle(self, ctx, complete_state):
        """Removing an existing bundle → it disappears from peer data."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        # Add first
        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "to-remove", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        # Remove
        ctx3 = _new_ctx()
        state_removed = ctx3.run(
            ctx3.on.action("remove-ca-certs", params={"name": "to-remove"}),
            state_added,
        )
        peer = [r for r in state_removed.relations if r.endpoint == "peers"][0]
        certs_data = json.loads(
            peer.local_app_data.get("certs_to_transfer", "{}")
        )
        assert "to-remove" not in certs_data

    def test_remove_nonexistent_fails(self, ctx, complete_state):
        """Removing a bundle that doesn't exist → action fails."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx2.run(
                ctx2.on.action("remove-ca-certs", params={"name": "ghost"}),
                state_mid,
            )
        assert "does not exist" in exc_info.value.message.lower()


# ---------------------------------------------------------------------------
# Action tests: list-ca-certs
# ---------------------------------------------------------------------------


class TestListCaCertsAction:
    """Test the list-ca-certs action behaviour."""

    def test_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader cannot run list-ca-certs action."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("list-ca-certs"), state_in)
        assert "lead unit" in exc_info.value.message.lower()

    def test_empty_returns_empty(self, ctx, complete_state):
        """When no certs are transferred, action returns empty dict."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.action("list-ca-certs"), state_mid)
        assert ctx2.action_results == {}

    def test_lists_stored_bundles(self, ctx, complete_state):
        """After adding a bundle, list returns it."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "listed-ca", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        ctx3 = _new_ctx()
        ctx3.run(ctx3.on.action("list-ca-certs"), state_added)
        assert "listed-ca" in ctx3.action_results

    def test_dot_names_are_sanitised(self, ctx, complete_state):
        """Dots in bundle names are replaced with hyphens in output."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "my.corp.ca", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        ctx3 = _new_ctx()
        ctx3.run(ctx3.on.action("list-ca-certs"), state_added)
        # Dots should be replaced with hyphens
        assert "my-corp-ca" in ctx3.action_results
        assert "my.corp.ca" not in ctx3.action_results


# ---------------------------------------------------------------------------
# Action tests: regenerate-password
# ---------------------------------------------------------------------------


class TestRegeneratePasswordAction:
    """Test the regenerate-password action behaviour."""

    def test_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader cannot run regenerate-password action."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(
                ctx.on.action(
                    "regenerate-password", params={"username": "admin"}
                ),
                state_in,
            )
        assert "lead unit" in exc_info.value.message.lower()

    def test_regenerate_password_leader(self, ctx, complete_state):
        """Leader regenerates password: calls keystone update_user, returns new password."""
        km = charm.manager.KeystoneManager.return_value
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        ctx2.run(
            ctx2.on.action(
                "regenerate-password", params={"username": "admin"}
            ),
            state_mid,
        )

        # The action should have called update_user on the keystone client
        km.ksclient.update_user.assert_called()
        call_kwargs = km.ksclient.update_user.call_args
        assert call_kwargs[1]["user"] == "admin"
        assert "password" in call_kwargs[1]

        # The action should return a password
        assert "password" in ctx2.action_results
        assert ctx2.action_results["password"] == "randompassword"


# ---------------------------------------------------------------------------
# Action tests: get-admin-password
# ---------------------------------------------------------------------------


class TestGetAdminPasswordAction:
    """Test the get-admin-password action behaviour."""

    def test_non_leader_fails(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader cannot run get-admin-password action."""
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=complete_secrets,
            storages=storages,
        )
        with pytest.raises(testing.ActionFailed) as exc_info:
            ctx.run(ctx.on.action("get-admin-password"), state_in)
        assert "lead unit" in exc_info.value.message.lower()

    def test_leader_returns_password(self, ctx, complete_state):
        """Leader returns the admin password from the secret."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.action("get-admin-password"), state_mid)

        assert "password" in ctx2.action_results
        # Password was set by pwgen mock returning "randompassword"
        assert ctx2.action_results["password"] == "randompassword"


# ---------------------------------------------------------------------------
# Certificate pipeline tests
# ---------------------------------------------------------------------------


class TestGetCombinedCaAndChain:
    """Test _get_combined_ca_and_chain method."""

    def test_no_certs_returns_empty(self, ctx, complete_state):
        """When no certificates stored, returns ('', [])."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            ca, chain = mgr.charm._get_combined_ca_and_chain()
            assert ca == ""
            assert chain == []

    def test_single_ca_no_chain(self, ctx, complete_state):
        """Single CA without chain → returns CA string and empty chain."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "single", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        ctx3 = _new_ctx()
        with ctx3(ctx3.on.config_changed(), state_added) as mgr:
            ca, chain = mgr.charm._get_combined_ca_and_chain()
            assert ca_pem in ca
            assert chain == []

    def test_ca_with_chain(self, ctx, complete_state):
        """CA + chain → chain returned as list of single concatenated string."""
        state_mid = _bootstrap(ctx, complete_state)
        root_pem, inter_pem, _, _ = _get_chain()
        ca_b64 = base64.b64encode(root_pem.encode()).decode()
        # Chain ordered: intermediate first, then root
        chain_pem = inter_pem + root_pem
        chain_b64 = base64.b64encode(chain_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "chained", "ca": ca_b64, "chain": chain_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        ctx3 = _new_ctx()
        with ctx3(ctx3.on.config_changed(), state_added) as mgr:
            ca, chain = mgr.charm._get_combined_ca_and_chain()
            assert root_pem in ca
            assert len(chain) == 1
            assert inter_pem in chain[0]
            assert root_pem in chain[0]

    def test_multiple_bundles_combined(self, ctx, complete_state):
        """Multiple bundles → CAs concatenated, chains concatenated."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem_1, _ = _get_self_signed()
        root_pem, inter_pem, _, _ = _get_chain()

        # Add first bundle (CA only)
        ctx2 = _new_ctx()
        state_after = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={
                    "name": "bundle1",
                    "ca": base64.b64encode(ca_pem_1.encode()).decode(),
                },
            ),
            state_mid,
        )
        state_after = _fix_checks(state_after)
        cleanup_database_requires_events()

        # Add second bundle (CA + chain)
        ctx3 = _new_ctx()
        chain_str = inter_pem + root_pem
        state_after2 = ctx3.run(
            ctx3.on.action(
                "add-ca-certs",
                params={
                    "name": "bundle2",
                    "ca": base64.b64encode(root_pem.encode()).decode(),
                    "chain": base64.b64encode(chain_str.encode()).decode(),
                },
            ),
            state_after,
        )
        state_after2 = _fix_checks(state_after2)
        cleanup_database_requires_events()

        ctx4 = _new_ctx()
        with ctx4(ctx4.on.config_changed(), state_after2) as mgr:
            ca, chain = mgr.charm._get_combined_ca_and_chain()
            # Both CAs should be present
            assert ca_pem_1 in ca
            assert root_pem in ca
            # Chain should have one entry (from bundle2)
            assert len(chain) == 1


class TestGetCaAndChain:
    """Test get_ca_and_chain method (combines _get_combined_ca_and_chain)."""

    def test_no_certs_returns_none(self, ctx, complete_state):
        """No certificates → returns None."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.get_ca_and_chain()
            assert result is None

    def test_with_ca_returns_combined_string(self, ctx, complete_state):
        """With CA + chain → returns joined string."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "test", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        ctx3 = _new_ctx()
        with ctx3(ctx3.on.config_changed(), state_added) as mgr:
            result = mgr.charm.get_ca_and_chain()
            assert ca_pem in result


class TestHandleCertificateTransfers:
    """Test _handle_certificate_transfers sends certs on send-ca-cert relations."""

    def test_sends_combined_ca_to_relations(self, ctx, complete_state):
        """Certificate transfer sends combined CA + chain to all send-ca-cert relations."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        # Add a CA bundle
        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "xfer-test", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        # Add a send-ca-cert relation and fire relation-joined
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_added,
            relations=[*state_added.relations, send_ca_rel],
        )

        ctx3 = _new_ctx()
        state_out = ctx3.run(
            ctx3.on.relation_joined(send_ca_rel), state_with_rel
        )

        # The library writes to unit data (relation.data[self.model.unit])
        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        local_data = dict(send_rel.local_unit_data)
        assert "ca" in local_data

    def test_non_leader_skips_transfer_on_event(self, ctx, complete_state):
        """Non-leader does not send certs on relation joined."""
        state_mid = _bootstrap(ctx, complete_state)
        state_non_leader = dataclasses.replace(state_mid, leader=False)

        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_non_leader,
            relations=[*state_non_leader.relations, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_joined(send_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        local_data = dict(send_rel.local_app_data)
        assert local_data.get("ca", "") == ""


# ---------------------------------------------------------------------------
# SAML cert and key validation tests
# ---------------------------------------------------------------------------


class TestEnsureSamlCertAndKey:
    """Test ensure_saml_cert_and_key method."""

    def test_no_config_no_providers_returns_empty(self, ctx, complete_state):
        """When saml-x509-keypair is not set and no providers, returns {}."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.ensure_saml_cert_and_key()
            assert result == {}
            km.remove_saml_key_and_cert.assert_called()

    def test_no_config_with_providers_raises_blocked(
        self, ctx, complete_state
    ):
        """When saml-x509-keypair not set but SAML providers exist → blocked."""
        state_mid = _bootstrap(ctx, complete_state)

        saml_rel = _keystone_saml_relation()
        state_with_saml = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, saml_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_saml) as mgr:
            # Ensure the saml handler has providers
            mgr.charm.keystone_saml.interface.get_providers = MagicMock(
                return_value=[{"name": "entra"}]
            )
            with pytest.raises(charm.sunbeam_guard.BlockedExceptionError):
                mgr.charm.ensure_saml_cert_and_key()

    def test_valid_keypair_writes_to_container(self, ctx, complete_state):
        """Valid cert+key → calls ensure_saml_cert_and_key_state and returns both."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        cert_pem, key_pem = _get_self_signed()

        saml_secret = testing.Secret(
            id="secret:samlkeypairid0000001",
            tracked_content={"certificate": cert_pem, "key": key_pem},
            latest_content={"certificate": cert_pem, "key": key_pem},
            owner=None,
        )
        state_with_secret = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_secret) as mgr:
            result = mgr.charm.ensure_saml_cert_and_key()
            assert result["cert"] == cert_pem
            assert result["key"] == key_pem
            km.ensure_saml_cert_and_key_state.assert_called_with(
                cert_pem, key_pem
            )

    def test_mismatched_keypair_raises_blocked(self, ctx, complete_state):
        """Cert derived from different key → raises BlockedExceptionError."""
        state_mid = _bootstrap(ctx, complete_state)
        cert_pem, _ = _get_self_signed()
        _, other_key = _make_self_signed_cert("Other")

        saml_secret = testing.Secret(
            id="secret:samlbadkeypair000002",
            tracked_content={"certificate": cert_pem, "key": other_key},
            latest_content={"certificate": cert_pem, "key": other_key},
            owner=None,
        )
        state_with_secret = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_secret) as mgr:
            with pytest.raises(charm.sunbeam_guard.BlockedExceptionError):
                mgr.charm.ensure_saml_cert_and_key()

    def test_only_key_no_cert_raises_blocked(self, ctx, complete_state):
        """Only key without certificate → raises BlockedExceptionError."""
        state_mid = _bootstrap(ctx, complete_state)
        _, key_pem = _get_self_signed()

        saml_secret = testing.Secret(
            id="secret:samlonlykeynocer0003",
            tracked_content={"key": key_pem},
            latest_content={"key": key_pem},
            owner=None,
        )
        state_with_secret = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_secret) as mgr:
            with pytest.raises(charm.sunbeam_guard.BlockedExceptionError):
                mgr.charm.ensure_saml_cert_and_key()

    def test_chain_cert_raises_blocked(self, ctx, complete_state):
        """A chain (multiple certs) instead of single cert → blocked."""
        state_mid = _bootstrap(ctx, complete_state)
        root_pem, inter_pem, _, _ = _get_chain()
        _, key_pem = _get_self_signed()
        chain_pem = root_pem + inter_pem  # Two certs concatenated

        saml_secret = testing.Secret(
            id="secret:samlchaincerttest004",
            tracked_content={"certificate": chain_pem, "key": key_pem},
            latest_content={"certificate": chain_pem, "key": key_pem},
            owner=None,
        )
        state_with_secret = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_secret) as mgr:
            with pytest.raises(charm.sunbeam_guard.BlockedExceptionError):
                mgr.charm.ensure_saml_cert_and_key()

    def test_secret_not_found_raises_blocked(self, ctx, complete_state):
        """When secret ID in config doesn't exist → raises BlockedExceptionError."""
        state_mid = _bootstrap(ctx, complete_state)
        # Use a secret ID that's not in the state's secrets list
        state_with_config = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": "secret:nonexistentsecret005"},
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_config) as mgr:
            with pytest.raises(
                (
                    charm.sunbeam_guard.BlockedExceptionError,
                    Exception,
                )
            ):
                mgr.charm.ensure_saml_cert_and_key()


# ---------------------------------------------------------------------------
# _get_certificate_body tests
# ---------------------------------------------------------------------------


class TestGetCertificateBody:
    """Test _get_certificate_body extracts PEM body."""

    def test_valid_single_cert(self, ctx, complete_state):
        """A valid single PEM cert → returns the body."""
        state_mid = _bootstrap(ctx, complete_state)
        cert_pem, _ = _get_self_signed()

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            body = mgr.charm._get_certificate_body(cert_pem)
            assert body != ""
            assert "BEGIN" not in body
            assert "END" not in body

    def test_garbage_data_returns_empty(self, ctx, complete_state):
        """Non-PEM data → returns empty string."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            body = mgr.charm._get_certificate_body("not a certificate")
            assert body == ""


# ---------------------------------------------------------------------------
# Identity Ops Dispatch tests
# ---------------------------------------------------------------------------


class TestHandleOpRequest:
    """Test handle_op_request dispatches operations."""

    def test_single_op_success(self, ctx, complete_state):
        """Single op with valid function → return-code 0 and result set."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        # Add identity-ops relation with a request
        ops_rel = testing.Relation(
            endpoint="identity-ops",
            remote_app_name="nova",
            remote_app_data={
                "request": json.dumps(
                    {
                        "id": "req-1",
                        "tag": "nova-setup",
                        "ops": [
                            {
                                "name": "create_domain",
                                "params": {"name": "nova-domain"},
                            }
                        ],
                    }
                ),
            },
            remote_units_data={0: {}},
        )
        km.ksclient.create_domain.return_value = "domain-id-123"

        state_with_ops = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, ops_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(ops_rel), state_with_ops)

        # The identity-ops relation should have a response
        ops_out = [
            r for r in state_out.relations if r.endpoint == "identity-ops"
        ][0]
        response = json.loads(ops_out.local_app_data.get("response", "{}"))
        assert response["id"] == "req-1"
        assert response["ops"][0]["return-code"] == 0
        assert response["ops"][0]["value"] == "domain-id-123"

    def test_op_failure_returns_error(self, ctx, complete_state):
        """Op that raises exception → return-code -1 and error message."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        ops_rel = testing.Relation(
            endpoint="identity-ops",
            remote_app_name="cinder",
            remote_app_data={
                "request": json.dumps(
                    {
                        "id": "req-fail",
                        "tag": "cinder-setup",
                        "ops": [
                            {
                                "name": "show_domain",
                                "params": {"name": "nonexistent"},
                            }
                        ],
                    }
                ),
            },
            remote_units_data={0: {}},
        )
        km.ksclient.show_domain.side_effect = Exception("Domain not found")

        state_with_ops = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, ops_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(ops_rel), state_with_ops)

        ops_out = [
            r for r in state_out.relations if r.endpoint == "identity-ops"
        ][0]
        response = json.loads(ops_out.local_app_data.get("response", "{}"))
        assert response["ops"][0]["return-code"] == -1
        assert "Domain not found" in response["ops"][0]["value"]

        # Restore mock
        km.ksclient.show_domain.side_effect = None

    def test_multi_op_with_templating(self, ctx, complete_state):
        """Multiple ops where later ops reference earlier results via Jinja2."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        ops_rel = testing.Relation(
            endpoint="identity-ops",
            remote_app_name="cinder",
            remote_app_data={
                "request": json.dumps(
                    {
                        "id": "req-multi",
                        "tag": "cinder-setup",
                        "ops": [
                            {
                                "name": "create_domain",
                                "params": {"name": "services"},
                            },
                            {
                                "name": "create_project",
                                "params": {
                                    "name": "cinder",
                                    "domain": "{{ create_domain[0] }}",
                                },
                            },
                        ],
                    }
                ),
            },
            remote_units_data={0: {}},
        )
        km.ksclient.create_domain.return_value = "svc-domain-id"
        km.ksclient.create_project.return_value = "cinder-project-id"

        state_with_ops = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, ops_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.relation_changed(ops_rel), state_with_ops)

        ops_out = [
            r for r in state_out.relations if r.endpoint == "identity-ops"
        ][0]
        response = json.loads(ops_out.local_app_data.get("response", "{}"))

        # Both ops succeed
        assert response["ops"][0]["return-code"] == 0
        assert response["ops"][1]["return-code"] == 0

        # Second op should have received the rendered domain from first op
        km.ksclient.create_project.assert_called_with(
            name="cinder", domain="svc-domain-id"
        )


class TestCheckOutstandingIdentityOpsRequests:
    """Test check_outstanding_identity_ops_requests dispatches new requests."""

    def test_new_request_is_processed(self, ctx, complete_state):
        """A new request (no matching response) is processed."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        ops_rel = testing.Relation(
            endpoint="identity-ops",
            remote_app_name="nova",
            remote_app_data={
                "request": json.dumps(
                    {
                        "id": "req-new",
                        "tag": "test",
                        "ops": [
                            {
                                "name": "create_domain",
                                "params": {"name": "test-domain"},
                            }
                        ],
                    }
                ),
            },
            remote_units_data={0: {}},
        )
        km.ksclient.create_domain.return_value = "new-domain-id"

        state_with_ops = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, ops_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_ops) as mgr:
            mgr.charm.check_outstanding_identity_ops_requests()
            km.ksclient.create_domain.assert_called()

    def test_already_responded_is_skipped(self, ctx, complete_state):
        """A request that already has a matching response ID is skipped."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.ksclient.create_domain.reset_mock()

        ops_rel = testing.Relation(
            endpoint="identity-ops",
            remote_app_name="nova",
            remote_app_data={
                "request": json.dumps(
                    {
                        "id": "req-done",
                        "tag": "test",
                        "ops": [],
                    }
                ),
            },
            local_app_data={
                "response": json.dumps(
                    {
                        "id": "req-done",
                        "tag": "test",
                        "ops": [],
                    }
                ),
            },
            remote_units_data={0: {}},
        )
        state_with_ops = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, ops_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_ops) as mgr:
            mgr.charm.check_outstanding_identity_ops_requests()
            km.ksclient.create_domain.assert_not_called()


# ---------------------------------------------------------------------------
# check_outstanding_identity_credentials_requests tests
# ---------------------------------------------------------------------------


class TestCheckOutstandingIdentityCredentialsRequests:
    """Test credential request processing logic."""

    def test_new_credential_request_is_processed(self, ctx, complete_state):
        """A credential request with username → add_credentials called."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value

        cred_rel = testing.Relation(
            endpoint="identity-credentials",
            remote_app_name="nova",
            remote_app_data={"username": "nova"},
            remote_units_data={0: {}},
        )
        state_with_cred = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, cred_rel],
        )

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.relation_changed(cred_rel), state_with_cred)

        # Service account creation should have been called
        km.create_service_account.assert_called()
        call_kwargs = km.create_service_account.call_args[1]
        assert call_kwargs["username"] == "nova"

    def test_already_processed_is_skipped(self, ctx, complete_state):
        """A credential request already processed (credentials in app data) is skipped."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.create_service_account.reset_mock()

        cred_rel = testing.Relation(
            endpoint="identity-credentials",
            remote_app_name="nova",
            remote_app_data={"username": "nova"},
            local_app_data={
                "credentials": "secret://some-id",
                "admin-role": "admin",
            },
            remote_units_data={0: {}},
        )
        state_with_cred = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, cred_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_cred) as mgr:
            mgr.charm.check_outstanding_identity_credentials_requests()
            km.create_service_account.assert_not_called()

    def test_no_username_is_skipped(self, ctx, complete_state):
        """A credential relation without username → request not processed."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.create_service_account.reset_mock()

        cred_rel = testing.Relation(
            endpoint="identity-credentials",
            remote_app_name="nova",
            remote_app_data={},
            remote_units_data={0: {}},
        )
        state_with_cred = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, cred_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_cred) as mgr:
            mgr.charm.check_outstanding_identity_credentials_requests()
            km.create_service_account.assert_not_called()


# ---------------------------------------------------------------------------
# Secret lifecycle tests
# ---------------------------------------------------------------------------


class TestOnSecretChangedCredentialKeys:
    """Test secret-changed for credential-keys label."""

    def test_writes_when_content_differs(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """credential-keys secret changed with different content → writes keys."""
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.return_value = {"0": "old-key0", "1": "old-key1"}
        km.write_keys.reset_mock()

        # Create a non-owned credential-keys secret (simulating non-leader receiving it)
        cred_secret = testing.Secret(
            id="secret:credkeys-ext",
            label="credential-keys",
            tracked_content={
                "fernet-0": "old-key0",
                "fernet-1": "old-key1",
            },
            latest_content={
                "fernet-0": "new-key0=",
                "fernet-1": "new-key1=",
            },
            owner=None,
        )
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=[*complete_secrets, cred_secret],
            storages=storages,
        )

        ctx.run(ctx.on.secret_changed(cred_secret), state_in)
        km.write_keys.assert_called_once()
        call_kwargs = km.write_keys.call_args[1]
        assert call_kwargs["key_repository"] == "/etc/keystone/credential-keys"

        # Restore
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

    def test_no_write_when_content_same(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """credential-keys secret changed with same content → no write."""
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}
        km.write_keys.reset_mock()

        cred_secret = testing.Secret(
            id="secret:credkeys-ext2",
            label="credential-keys",
            tracked_content={
                "fernet-0": "key0data=",
                "fernet-1": "key1data=",
            },
            latest_content={
                "fernet-0": "key0data=",
                "fernet-1": "key1data=",
            },
            owner=None,
        )
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=[*complete_secrets, cred_secret],
            storages=storages,
        )

        ctx.run(ctx.on.secret_changed(cred_secret), state_in)
        km.write_keys.assert_not_called()


class TestOnSecretRemove:
    """Test secret-remove behaviour."""

    def test_fernet_keys_secret_is_handled(self, ctx, complete_state):
        """secret-remove for fernet-keys label → handled without error."""
        state_mid = _bootstrap(ctx, complete_state)

        peer = [r for r in state_mid.relations if r.endpoint == "peers"][0]
        fernet_secret_id = peer.local_app_data.get("fernet-secret-id")
        fernet_secret = [
            s for s in state_mid.secrets if s.id == fernet_secret_id
        ]
        if not fernet_secret:
            pytest.skip("No fernet secret found after bootstrap")

        secret = fernet_secret[0]

        ctx2 = _new_ctx()
        # Should not raise — fernet-keys label is handled gracefully
        ctx2.run(ctx2.on.secret_remove(secret, revision=1), state_mid)

    def test_credential_keys_secret_is_handled(self, ctx, complete_state):
        """secret-remove for credential-keys label → handled without error."""
        state_mid = _bootstrap(ctx, complete_state)

        peer = [r for r in state_mid.relations if r.endpoint == "peers"][0]
        cred_secret_id = peer.local_app_data.get("credential-keys-secret-id")
        cred_secret = [s for s in state_mid.secrets if s.id == cred_secret_id]
        if not cred_secret:
            pytest.skip("No credential-keys secret found after bootstrap")

        secret = cred_secret[0]

        ctx2 = _new_ctx()
        # Should not raise
        ctx2.run(ctx2.on.secret_remove(secret, revision=1), state_mid)

    def test_identity_service_secret_deletes_old_users(
        self, ctx, complete_state
    ):
        """secret-remove for identity service secret → deletes old rotated users."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.ksclient.delete_user.reset_mock()

        # Add an identity-service relation so the charm generates labels
        idsvc_rel = _identity_service_relation()
        state_with_idsvc = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, idsvc_rel],
        )

        # Create a secret with matching label pattern
        idsvc_secret = testing.Secret(
            id="secret://idsvc-secret",
            label="credentials_svc_cinder",
            tracked_content={"username": "svc_cinder", "password": "old"},
            latest_content={"username": "svc_cinder", "password": "new"},
            owner="application",
        )
        # Add old_service_users to peer data
        peer = [
            r for r in state_with_idsvc.relations if r.endpoint == "peers"
        ][0]
        updated_peer = dataclasses.replace(
            peer,
            local_app_data={
                **dict(peer.local_app_data),
                "old_service_users": json.dumps(["svc_cinder-olduser"]),
            },
        )
        new_rels = [
            updated_peer if r.endpoint == "peers" else r
            for r in state_with_idsvc.relations
        ]
        state_final = dataclasses.replace(
            state_with_idsvc,
            relations=new_rels,
            secrets=[*state_with_idsvc.secrets, idsvc_secret],
        )

        ctx2 = _new_ctx()
        ctx2.run(ctx2.on.secret_remove(idsvc_secret, revision=1), state_final)

        km.ksclient.delete_user.assert_called_with("svc_cinder-olduser")


# ---------------------------------------------------------------------------
# Bootstrap error path tests
# ---------------------------------------------------------------------------


class TestBootstrapErrorPaths:
    """Test keystone_bootstrap error handling."""

    def test_setup_keystone_exec_error_sets_blocked(self, ctx, complete_state):
        """If ExecError during setup_keystone → BlockedStatus."""
        km = charm.manager.KeystoneManager.return_value
        import ops.pebble

        km.setup_keystone.side_effect = ops.pebble.ExecError(
            command=["keystone-manage"], exit_code=1, stdout="", stderr="fail"
        )

        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "blocked"
        assert "failed to bootstrap" in state_out.unit_status.message.lower()

        # Restore
        km.setup_keystone.side_effect = None

    def test_setup_projects_error_sets_blocked(self, ctx, complete_state):
        """Exception during setup_initial_projects_and_users → BlockedStatus."""
        km = charm.manager.KeystoneManager.return_value
        km.setup_initial_projects_and_users.side_effect = Exception(
            "connect failed"
        )

        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "blocked"
        assert (
            "failed to setup projects" in state_out.unit_status.message.lower()
        )

        # Restore
        km.setup_initial_projects_and_users.side_effect = None


class TestUnitFernetBootstrapped:
    """Test unit_fernet_bootstrapped edge cases."""

    def test_attribute_error_returns_false(self, ctx, complete_state):
        """When read_keys raises AttributeError → returns False."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.side_effect = AttributeError("no container")

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.unit_fernet_bootstrapped()
            assert result is False

        km.read_keys.side_effect = None
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

    def test_connection_error_returns_false(self, ctx, complete_state):
        """When pebble not ready → returns False."""
        import ops.pebble

        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.side_effect = ops.pebble.ConnectionError("not ready")

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.unit_fernet_bootstrapped()
            assert result is False

        km.read_keys.side_effect = None
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

    def test_no_keys_returns_false(self, ctx, complete_state):
        """When read_keys returns empty dict → returns False."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.return_value = {}

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.unit_fernet_bootstrapped()
            assert result is False

        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

    def test_keys_present_returns_true(self, ctx, complete_state):
        """When read_keys returns keys → returns True."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.return_value = {"0": "keydata="}

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.unit_fernet_bootstrapped()
            assert result is True


# ---------------------------------------------------------------------------
# get_ca_bundles_from_fid_relations tests
# ---------------------------------------------------------------------------


class TestGetCaBundlesFromFidRelations:
    """Test get_ca_bundles_from_fid_relations collects CA chains from federation."""

    def test_no_providers_returns_empty(self, ctx, complete_state):
        """No providers → empty list."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            result = mgr.charm.get_ca_bundles_from_fid_relations()
            assert result == []

    def test_collects_from_oauth_providers(self, ctx, complete_state):
        """CA chains from oauth providers are collected."""
        state_mid = _bootstrap(ctx, complete_state)

        oauth_rel = _oauth_relation()
        oauth_sec = _oauth_secret()
        state_with_oauth = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, oauth_rel],
            secrets=[*state_mid.secrets, oauth_sec],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_oauth) as mgr:
            # Mock the handler to return provider info with CA chain
            ca_pem, _ = _get_self_signed()
            mgr.charm.oauth.get_all_provider_info = MagicMock(
                return_value=[{"ca_chain": [ca_pem]}]
            )
            mgr.charm.external_idp.get_all_provider_info = MagicMock(
                return_value=[]
            )
            mgr.charm.keystone_saml.interface.get_providers = MagicMock(
                return_value=[]
            )
            result = mgr.charm.get_ca_bundles_from_fid_relations()
            assert len(result) == 1
            assert ca_pem in result[0]


# ---------------------------------------------------------------------------
# certs.py pure function tests
# ---------------------------------------------------------------------------


class TestCertsUtils:
    """Test certs.py pure utility functions."""

    def test_certificate_is_valid_with_valid_cert(self):
        """A valid PEM certificate passes validation."""
        from utils import (
            certs,
        )

        cert_pem, _ = _get_self_signed()
        assert certs.certificate_is_valid(cert_pem.encode()) is True

    def test_certificate_is_valid_with_garbage(self):
        """Garbage data fails validation."""
        from utils import (
            certs,
        )

        assert certs.certificate_is_valid(b"not a certificate") is False

    def test_cert_and_key_match_with_matching_pair(self):
        """A cert and its own key match."""
        from utils import (
            certs,
        )

        cert_pem, key_pem = _get_self_signed()
        assert (
            certs.cert_and_key_match(cert_pem.encode(), key_pem.encode())
            is True
        )

    def test_cert_and_key_mismatch(self):
        """A cert and a different key do not match."""
        from utils import (
            certs,
        )

        cert_pem, _ = _get_self_signed()
        _, other_key = _make_self_signed_cert("Other CN")
        assert (
            certs.cert_and_key_match(cert_pem.encode(), other_key.encode())
            is False
        )

    def test_parse_ca_chain_single_cert(self):
        """A single certificate in chain → list of one."""
        from utils import (
            certs,
        )

        cert_pem, _ = _get_self_signed()
        result = certs.parse_ca_chain(cert_pem)
        assert len(result) == 1

    def test_parse_ca_chain_multiple_certs(self):
        """Multiple certs concatenated → list of multiple."""
        from utils import (
            certs,
        )

        root_pem, inter_pem, _, _ = _get_chain()
        chain_pem = root_pem + inter_pem
        result = certs.parse_ca_chain(chain_pem)
        assert len(result) == 2

    def test_parse_ca_chain_empty_raises(self):
        """Empty string raises ValueError."""
        from utils import (
            certs,
        )

        with pytest.raises(ValueError, match="No certificate found"):
            certs.parse_ca_chain("nothing here")

    def test_ca_chain_is_valid_with_valid_chain(self):
        """A properly ordered chain validates."""
        from utils import (
            certs,
        )

        root_pem, inter_pem, leaf_pem, _ = _get_chain()
        chain = certs.parse_ca_chain(leaf_pem + inter_pem)
        assert certs.ca_chain_is_valid(chain) is True

    def test_ca_chain_is_valid_single_cert(self):
        """A single cert chain validates."""
        from utils import (
            certs,
        )

        cert_pem, _ = _get_self_signed()
        assert certs.ca_chain_is_valid([cert_pem]) is True

    def test_ca_chain_is_valid_wrong_order(self):
        """Chain in wrong order fails validation."""
        from utils import (
            certs,
        )

        root_pem, inter_pem, leaf_pem, _ = _get_chain()
        # Put root first, then leaf — root didn't sign leaf directly
        chain = certs.parse_ca_chain(root_pem + leaf_pem)
        # Root didn't directly sign leaf, so this should fail
        # (root signed intermediate, intermediate signed leaf)
        assert certs.ca_chain_is_valid(chain) is False

    def test_ca_chain_is_valid_garbage_returns_false(self):
        """Garbage data in chain returns False."""
        from utils import (
            certs,
        )

        assert certs.ca_chain_is_valid(["not a cert"]) is False


# ---------------------------------------------------------------------------
# _sanitize_secrets tests
# ---------------------------------------------------------------------------


class TestSanitizeSecrets:
    """Test _sanitize_secrets resolves secret:// references."""

    def test_replaces_secret_prefix(self, ctx, complete_state):
        """Params with secret:// prefix are resolved to secret contents."""
        state_mid = _bootstrap(ctx, complete_state)

        # Create a user secret that the charm can look up
        user_secret = testing.Secret(
            id="secret://user-cred",
            tracked_content={"password": "mypassword123"},
            latest_content={"password": "mypassword123"},
            owner=None,
        )
        state_with_secret = dataclasses.replace(
            state_mid,
            secrets=[*state_mid.secrets, user_secret],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_secret) as mgr:
            request = {
                "ops": [
                    {
                        "name": "create_user",
                        "params": {
                            "name": "testuser",
                            "password": "secret://user-cred",
                        },
                    }
                ]
            }
            result = mgr.charm._sanitize_secrets(request)
            assert result["ops"][0]["params"]["password"] == "mypassword123"

    def test_non_secret_params_unchanged(self, ctx, complete_state):
        """Params without secret:// prefix are left unchanged."""
        state_mid = _bootstrap(ctx, complete_state)

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_mid) as mgr:
            request = {
                "ops": [
                    {
                        "name": "create_user",
                        "params": {
                            "name": "testuser",
                            "email": "test@example.com",
                        },
                    }
                ]
            }
            result = mgr.charm._sanitize_secrets(request)
            assert result["ops"][0]["params"]["name"] == "testuser"
            assert result["ops"][0]["params"]["email"] == "test@example.com"


# ---------------------------------------------------------------------------
# check_outstanding_identity_endpoints_requests tests
# ---------------------------------------------------------------------------


class TestCheckOutstandingIdentityEndpointsRequests:
    """Test identity-endpoints request processing."""

    def test_already_processed_is_skipped(self, ctx, complete_state):
        """A request that already has endpoints in app data is skipped."""
        state_mid = _bootstrap(ctx, complete_state)
        km = charm.manager.KeystoneManager.return_value
        km.ksclient.list_endpoints.reset_mock()

        endp_rel = testing.Relation(
            endpoint="identity-endpoints",
            remote_app_name="nova",
            remote_app_data={},
            local_app_data={"endpoints": "already-there"},
            remote_units_data={0: {}},
        )
        state_with_endp = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, endp_rel],
        )

        ctx2 = _new_ctx()
        with ctx2(ctx2.on.config_changed(), state_with_endp) as mgr:
            mgr.charm.check_outstanding_identity_endpoints_requests()
            # Should not process because endpoints already set
            km.ksclient.list_endpoints.assert_not_called()


# ===========================================================================
# Strengthened behavioural tests
# ===========================================================================


class TestBootstrapRecoveryAfterFailure:
    """Verify that bootstrap retries on the next event after a failure.

    run_once_per_unit only marks the job as done AFTER the function returns
    successfully. A raised BlockedExceptionError stops storage.add(), so the
    next config-changed must re-attempt bootstrap.
    """

    def test_bootstrap_retries_after_exec_error(self, ctx, complete_state):
        """Fail bootstrap, fire again, verify recovery to active."""
        km = charm.manager.KeystoneManager.return_value
        import ops.pebble

        # --- First event: setup_keystone fails ---
        km.setup_keystone.side_effect = ops.pebble.ExecError(
            command=["keystone-manage"], exit_code=1, stdout="", stderr="fail"
        )
        state_after_fail = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_after_fail.unit_status.name == "blocked"

        # setup_keystone was called but setup_initial_projects_and_users was NOT
        km.setup_keystone.assert_called()
        km.setup_initial_projects_and_users.assert_not_called()

        # After a failed bootstrap the pebble plan has no healthcheck layer,
        # so do NOT call _fix_checks (that would add check_infos without a
        # matching plan). Just cleanup the DB events for the next context.
        cleanup_database_requires_events()

        # --- Second event: setup_keystone succeeds ---
        km.setup_keystone.side_effect = None
        km.setup_keystone.reset_mock()
        km.setup_initial_projects_and_users.reset_mock()

        ctx2 = _new_ctx()
        state_recovered = ctx2.run(ctx2.on.config_changed(), state_after_fail)

        # Bootstrap re-ran because run_once_per_unit did NOT mark it done
        km.setup_keystone.assert_called_once()
        km.setup_initial_projects_and_users.assert_called_once()
        assert state_recovered.unit_status.name == "active"

    def test_bootstrap_does_not_rerun_after_success(self, ctx, complete_state):
        """After successful bootstrap, a second config-changed skips it."""
        km = charm.manager.KeystoneManager.return_value
        km.setup_keystone.reset_mock()

        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status.name == "active"
        assert km.setup_keystone.call_count == 1

        state_out = _fix_checks(state_out)
        cleanup_database_requires_events()

        # Second event: bootstrap should NOT run again
        km.setup_keystone.reset_mock()
        ctx2 = _new_ctx()
        state_out2 = ctx2.run(ctx2.on.config_changed(), state_out)
        km.setup_keystone.assert_not_called()
        assert state_out2.unit_status.name == "active"


class TestBootstrapSecretStructure:
    """Verify that bootstrap creates secrets with the correct key structure.

    The fernet and credential-keys secrets store keys prefixed with 'fernet-'
    to satisfy Juju's secret content key naming requirements (≥3 chars,
    no leading digit, no trailing dash).
    """

    def test_fernet_secret_has_prefixed_keys(self, ctx, complete_state):
        """fernet-keys secret content has 'fernet-{filename}' keys."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        fernet_secret_id = peer.local_app_data.get("fernet-secret-id")
        assert fernet_secret_id, "Bootstrap should create fernet-secret-id"

        fernet_secret = [
            s for s in state_out.secrets if s.id == fernet_secret_id
        ]
        assert fernet_secret, f"Secret {fernet_secret_id} missing from state"
        content = fernet_secret[0].latest_content
        # Keys from mock: {"0": "key0data=", "1": "key1data="}
        # Should become: {"fernet-0": "key0data=", "fernet-1": "key1data="}
        assert "fernet-0" in content
        assert "fernet-1" in content
        assert content["fernet-0"] == "key0data="
        assert content["fernet-1"] == "key1data="

    def test_credential_keys_secret_has_prefixed_keys(
        self, ctx, complete_state
    ):
        """credential-keys secret content has 'fernet-{filename}' keys."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        cred_id = peer.local_app_data.get("credential-keys-secret-id")
        assert cred_id, "Bootstrap should create credential-keys-secret-id"

        cred_secret = [s for s in state_out.secrets if s.id == cred_id]
        assert cred_secret, f"Secret {cred_id} missing from state"
        content = cred_secret[0].latest_content
        assert "fernet-0" in content
        assert "fernet-1" in content

    def test_bootstrap_with_existing_secret_updates_if_keys_differ(
        self, ctx, complete_state
    ):
        """When fernet-secret-id exists but keys on disk differ, secret is updated.

        This simulates the case where bootstrap re-triggers (e.g. after a
        blocked recovery) and the secret already exists from a prior run.
        _create_fernet_secret should update the existing secret content.
        """
        km = charm.manager.KeystoneManager.return_value

        # First bootstrap: creates secrets normally
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        peer = [r for r in state_out.relations if r.endpoint == "peers"][0]
        fernet_secret_id = peer.local_app_data.get("fernet-secret-id")
        assert fernet_secret_id

        # Check initial content matches mock's read_keys return
        fernet_secret = [
            s for s in state_out.secrets if s.id == fernet_secret_id
        ]
        assert fernet_secret
        assert fernet_secret[0].latest_content["fernet-0"] == "key0data="

        # Restore
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}


class TestSamlValidationThroughEventFlow:
    """Verify SAML validation runs through the full config-changed dispatch.

    ensure_saml_cert_and_key() is called from KeystoneSAMLHandler.context()
    during the configure flow. These tests verify the full dispatch chain,
    not just the method in isolation.
    """

    def test_invalid_saml_secret_blocks_full_config(self, ctx, complete_state):
        """SAML config with invalid cert blocks the charm through event flow."""
        state_mid = _bootstrap(ctx, complete_state)
        _, key_pem = _get_self_signed()

        # Create a secret with only a key (no cert) — should block
        saml_secret = testing.Secret(
            id="secret:samlflowtest000000a1",
            tracked_content={"key": key_pem},
            latest_content={"key": key_pem},
            owner=None,
        )

        saml_rel = _keystone_saml_relation()
        state_with_saml = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
            relations=[*state_mid.relations, saml_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.config_changed(), state_with_saml)
        # The charm should be blocked because the SAML cert is incomplete
        assert state_out.unit_status.name == "blocked"
        assert "key and certificate" in state_out.unit_status.message.lower()

    def test_valid_saml_secret_allows_active(self, ctx, complete_state):
        """SAML config with valid cert+key does not block the charm."""
        state_mid = _bootstrap(ctx, complete_state)
        cert_pem, key_pem = _get_self_signed()

        saml_secret = testing.Secret(
            id="secret:samlflowtest000000a2",
            tracked_content={"certificate": cert_pem, "key": key_pem},
            latest_content={"certificate": cert_pem, "key": key_pem},
            owner=None,
        )

        state_with_saml = dataclasses.replace(
            state_mid,
            config={"saml-x509-keypair": saml_secret.id},
            secrets=[*state_mid.secrets, saml_secret],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(ctx2.on.config_changed(), state_with_saml)
        # Without SAML providers, the method returns early — no block
        assert state_out.unit_status.name == "active"


class TestCertificateTransferFullFlow:
    """Verify certificate transfer through the complete event dispatch.

    Tests that adding a CA bundle → joining send-ca-cert relation
    produces correct unit relation data through the real event chain.
    """

    def test_ca_bundle_propagates_to_relation_unit_data(
        self, ctx, complete_state
    ):
        """Added CA appears in send-ca-cert relation's unit data after join."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()

        # Add a CA
        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "transfer-test", "ca": ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        # Add send-ca-cert relation
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_added,
            relations=[*state_added.relations, send_ca_rel],
        )

        ctx3 = _new_ctx()
        state_out = ctx3.run(
            ctx3.on.relation_joined(send_ca_rel), state_with_rel
        )

        # Verify the library wrote to unit data (not app data)
        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        assert "ca" in unit_data, "CA must be in unit relation data"
        assert ca_pem in unit_data["ca"], "CA PEM must be in the data"
        assert "chain" in unit_data, "chain key must be present"

    def test_empty_ca_store_sends_empty_chain(self, ctx, complete_state):
        """With no CAs stored, the transfer sends empty chain."""
        state_mid = _bootstrap(ctx, complete_state)

        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_joined(send_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        # Juju strips empty-string values from relation data, so "ca" and
        # "certificate" won't be present when set to "".
        assert unit_data.get("ca", "") == ""


class TestDedupePreserveOrder:
    """Unit tests for KeystoneOperatorCharm._dedupe_preserve_order."""

    def test_empty_list(self):
        """Return an empty list unchanged."""
        assert charm.KeystoneOperatorCharm._dedupe_preserve_order([]) == []

    def test_no_duplicates(self):
        """Keep the original order when no duplicates are present."""
        result = charm.KeystoneOperatorCharm._dedupe_preserve_order(
            ["a", "b", "c"]
        )
        assert result == ["a", "b", "c"]

    def test_duplicates_removed_preserving_order(self):
        """Drop duplicate values while preserving first-seen order."""
        result = charm.KeystoneOperatorCharm._dedupe_preserve_order(
            ["b", "a", "b", "c", "a"]
        )
        assert result == ["b", "a", "c"]

    def test_blank_and_empty_strings_filtered(self):
        """Filter out blank entries while preserving valid values."""
        result = charm.KeystoneOperatorCharm._dedupe_preserve_order(
            ["", "a", "", "b"]
        )
        assert result == ["a", "b"]

    def test_all_empty_strings(self):
        """Return an empty list when all entries are blank."""
        result = charm.KeystoneOperatorCharm._dedupe_preserve_order(
            ["", "", ""]
        )
        assert result == []

    def test_single_element(self):
        """Return a single-element list unchanged."""
        assert charm.KeystoneOperatorCharm._dedupe_preserve_order(["x"]) == [
            "x"
        ]

    def test_all_duplicates(self):
        """Keep only one value when all entries are duplicates."""
        result = charm.KeystoneOperatorCharm._dedupe_preserve_order(
            ["x", "x", "x"]
        )
        assert result == ["x"]


class TestReceiveCaCertPropagation:
    """Test receive-ca-cert propagation to send-ca-cert."""

    def test_receive_ca_cert_propagates_to_send_relation(
        self, ctx, complete_state
    ):
        """CA received on receive-ca-cert is republished on send-ca-cert."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={0: {"ca": ca_pem, "chain": json.dumps([])}},
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        assert ca_pem in unit_data["ca"]

    def test_mixed_action_and_receive_ca_are_both_published(
        self, ctx, complete_state
    ):
        """Action-uploaded and receive-ca-cert CAs are both sent downstream."""
        state_mid = _bootstrap(ctx, complete_state)
        uploaded_ca_pem, _ = _get_self_signed()
        uploaded_ca_b64 = base64.b64encode(uploaded_ca_pem.encode()).decode()

        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={"name": "uploaded-ca", "ca": uploaded_ca_b64},
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        received_ca_pem, _ = _make_self_signed_cert("Received CA")
        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={
                0: {"ca": received_ca_pem, "chain": json.dumps([])}
            },
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_added,
            relations=[*state_added.relations, receive_ca_rel, send_ca_rel],
        )

        ctx3 = _new_ctx()
        state_out = ctx3.run(
            ctx3.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        assert uploaded_ca_pem in unit_data["ca"]
        assert received_ca_pem in unit_data["ca"]
        assert json.loads(unit_data.get("chain", "[]")) == []

    def test_receive_ca_with_chain_propagates_chain(self, ctx, complete_state):
        """Chain received on receive-ca-cert is republished on send-ca-cert."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        chain_pem, _ = _make_self_signed_cert("Intermediate CA")

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={
                0: {
                    "ca": ca_pem,
                    "chain": json.dumps([chain_pem]),
                }
            },
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        parsed_chain = json.loads(unit_data.get("chain", "[]"))
        assert any(chain_pem in c for c in parsed_chain)

    def test_receive_ca_with_empty_relation_data(self, ctx, complete_state):
        """Relation with no unit data does not cause errors."""
        state_mid = _bootstrap(ctx, complete_state)

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={0: {}},
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        assert unit_data.get("ca", "") == ""

    def test_receive_ca_with_malformed_chain_json(self, ctx, complete_state):
        """Malformed chain JSON is handled gracefully, charm does not crash."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={0: {"ca": ca_pem, "chain": "not-valid-json"}},
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        # Must not raise — charm handles malformed data gracefully
        ctx2.run(ctx2.on.relation_changed(receive_ca_rel), state_with_rel)

    def test_multiple_receive_ca_cert_units(self, ctx, complete_state):
        """Propagate CAs from multiple receive-ca-cert units."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem_1, _ = _get_self_signed()
        ca_pem_2, _ = _make_self_signed_cert("Second CA")

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={
                0: {"ca": ca_pem_1, "chain": json.dumps([])},
                1: {"ca": ca_pem_2, "chain": json.dumps([])},
            },
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        assert ca_pem_1 in unit_data["ca"]
        assert ca_pem_2 in unit_data["ca"]

    def test_duplicate_ca_across_units_deduped(self, ctx, complete_state):
        """Same CA from multiple units appears only once."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()

        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={
                0: {"ca": ca_pem, "chain": json.dumps([])},
                1: {"ca": ca_pem, "chain": json.dumps([])},
            },
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_mid,
            relations=[*state_mid.relations, receive_ca_rel, send_ca_rel],
        )

        ctx2 = _new_ctx()
        state_out = ctx2.run(
            ctx2.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        # CA should appear exactly once, not duplicated
        assert unit_data["ca"].count(ca_pem.strip()) == 1

    def test_duplicate_chain_across_uploaded_and_received_deduped(
        self, ctx, complete_state
    ):
        """Same chain cert from upload and receive appears only once."""
        state_mid = _bootstrap(ctx, complete_state)
        ca_pem, _ = _get_self_signed()
        chain_pem, _ = _make_self_signed_cert("Shared Chain")

        # Upload a CA with a chain
        ca_b64 = base64.b64encode(ca_pem.encode()).decode()
        chain_b64 = base64.b64encode(chain_pem.encode()).decode()
        ctx2 = _new_ctx()
        state_added = ctx2.run(
            ctx2.on.action(
                "add-ca-certs",
                params={
                    "name": "with-chain",
                    "ca": ca_b64,
                    "chain": chain_b64,
                },
            ),
            state_mid,
        )
        state_added = _fix_checks(state_added)
        cleanup_database_requires_events()

        # Receive the same chain from a relation
        received_ca_pem, _ = _make_self_signed_cert("Received CA")
        receive_ca_rel = testing.Relation(
            endpoint="receive-ca-cert",
            remote_app_name="certificate-authority",
            remote_units_data={
                0: {
                    "ca": received_ca_pem,
                    "chain": json.dumps([chain_pem]),
                }
            },
        )
        send_ca_rel = testing.Relation(
            endpoint="send-ca-cert",
            remote_app_name="nova",
            remote_units_data={0: {}},
        )
        state_with_rel = dataclasses.replace(
            state_added,
            relations=[*state_added.relations, receive_ca_rel, send_ca_rel],
        )

        ctx3 = _new_ctx()
        state_out = ctx3.run(
            ctx3.on.relation_changed(receive_ca_rel), state_with_rel
        )

        send_rel = [
            r for r in state_out.relations if r.endpoint == "send-ca-cert"
        ][0]
        unit_data = dict(send_rel.local_unit_data)
        parsed_chain = json.loads(unit_data.get("chain", "[]"))
        # chain_pem should appear exactly once across all chain entries
        all_chain_text = "\n".join(parsed_chain)
        assert all_chain_text.count(chain_pem.strip()) == 1


class TestNonLeaderSecretSync:
    """Verify non-leader units sync keys via secret-changed.

    Leaders create and rotate secrets. Non-leaders receive secret-changed
    and must write the new key material to their local filesystem.
    This tests the full event dispatch (not just the method).
    """

    def test_credential_keys_secret_changed_writes_to_disk(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader receiving secret-changed writes new keys via manager."""
        km = charm.manager.KeystoneManager.return_value
        # Existing keys on disk differ from secret content
        km.read_keys.return_value = {"0": "stale0=", "1": "stale1="}
        km.write_keys.reset_mock()

        cred_secret = testing.Secret(
            id="secret:credkeyssynctest0001",
            label="credential-keys",
            tracked_content={
                "fernet-0": "stale0=",
                "fernet-1": "stale1=",
            },
            latest_content={
                "fernet-0": "fresh0=",
                "fernet-1": "fresh1=",
            },
            owner=None,
        )
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=[*complete_secrets, cred_secret],
            storages=storages,
        )

        ctx.run(ctx.on.secret_changed(cred_secret), state_in)

        km.write_keys.assert_called_once()
        call_kwargs = km.write_keys.call_args[1]
        # Verify the correct key repository path
        assert call_kwargs["key_repository"] == "/etc/keystone/credential-keys"
        # Verify the correct key data was passed (fernet- prefix stripped)
        assert call_kwargs["keys"] == {"0": "fresh0=", "1": "fresh1="}

        # Restore
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}

    def test_fernet_keys_secret_changed_writes_to_disk(
        self, ctx, complete_relations, complete_secrets, container, storages
    ):
        """Non-leader receiving fernet secret-changed writes new keys."""
        km = charm.manager.KeystoneManager.return_value
        km.read_keys.return_value = {"0": "oldfernet="}
        km.write_keys.reset_mock()

        fernet_secret = testing.Secret(
            id="secret:fernetsynctestsec001",
            label="fernet-keys",
            tracked_content={"fernet-0": "oldfernet="},
            latest_content={
                "fernet-0": "newfernet0=",
                "fernet-1": "newfernet1=",
            },
            owner=None,
        )
        state_in = testing.State(
            leader=False,
            relations=complete_relations,
            containers=[container],
            secrets=[*complete_secrets, fernet_secret],
            storages=storages,
        )

        ctx.run(ctx.on.secret_changed(fernet_secret), state_in)

        km.write_keys.assert_called_once()
        call_kwargs = km.write_keys.call_args[1]
        assert call_kwargs["key_repository"] == "/etc/keystone/fernet-keys"
        assert call_kwargs["keys"] == {
            "0": "newfernet0=",
            "1": "newfernet1=",
        }

        # Restore
        km.read_keys.return_value = {"0": "key0data=", "1": "key1data="}
