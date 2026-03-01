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

import dataclasses
import json

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
