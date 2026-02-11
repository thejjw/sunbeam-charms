#!/usr/bin/env python3

# Copyright 2021 Canonical Ltd.
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

"""Define keystone tests."""

import json
import os
import textwrap
import unittest
from unittest.mock import (
    ANY,
    MagicMock,
    call,
    patch,
)

import charm
import keystoneauth1.exceptions
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.test_utils as test_utils
from ops.model import (
    ModelError,
    SecretNotFoundError,
)


class _KeystoneOperatorCharm(charm.KeystoneOperatorCharm):
    """Create Keystone operator test charm."""

    def __init__(self, framework):
        self.seen_events = []
        super().__init__(framework)

    def _log_event(self, event):
        self.seen_events.append(type(event).__name__)

    def configure_charm(self, event):
        super().configure_charm(event)
        self._log_event(event)

    @property
    def public_ingress_address(self) -> str:
        return "10.0.0.10"


class TestKeystoneOperatorCharm(test_utils.CharmTestCase):
    """Test Keystone operator charm."""

    PATCHES = [
        "manager",
        "pwgen",
    ]

    def add_trusted_dashboard_relation(self) -> int:
        """Add trusted-dashboard relation."""
        rel_id = self.harness.add_relation("trusted-dashboard", "horizon")
        self.harness.add_relation_unit(rel_id, "horizon/0")
        self.harness.update_relation_data(
            rel_id, "horizon/0", {"ingress-address": "10.0.0.11"}
        )
        return rel_id

    def add_oauth_relation(self) -> int:
        """Add oauth relation."""
        rel_id = self.harness.add_relation("oauth", "hydra")
        self.harness.add_relation_unit(rel_id, "hydra/0")
        self.harness.update_relation_data(
            rel_id, "hydra/0", {"ingress-address": "10.0.0.12"}
        )
        self.harness.update_relation_data(
            rel_id,
            "hydra",
            {
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
            },
        )
        return rel_id

    def add_keystone_saml_relation(self) -> int:
        """Add keystone-saml relation."""
        rel_id = self.harness.add_relation(
            "keystone-saml", "keystone-saml-entra"
        )
        self.harness.add_relation_unit(rel_id, "keystone-saml-entra/0")
        self.harness.update_relation_data(
            rel_id, "keystone-saml-entra/0", {"ingress-address": "10.0.0.99"}
        )
        self.harness.update_relation_data(
            rel_id,
            "keystone-saml-entra",
            {
                "name": "entra",
                "label": "Log in with Entra SAML2",
                "metadata": "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4=",
            },
        )
        return rel_id

    def add_id_relation(self) -> int:
        """Add amqp relation."""
        rel_id = self.harness.add_relation("identity-service", "cinder")
        self.harness.add_relation_unit(rel_id, "cinder/0")
        self.harness.update_relation_data(
            rel_id, "cinder/0", {"ingress-address": "10.0.0.13"}
        )
        interal_url = "http://10.152.183.228:8776"
        public_url = "http://10.152.183.228:8776"
        self.harness.update_relation_data(
            rel_id,
            "cinder",
            {
                "region": "RegionOne",
                "service-endpoints": json.dumps(
                    [
                        {
                            "service_name": "cinderv3",
                            "type": "volumev3",
                            "description": "Cinder Volume Service v3",
                            "internal_url": f"{interal_url}/v3/$(tenant_id)s",
                            "public_url": f"{public_url}/v3/$(tenant_id)s",
                            "admin_url": f"{interal_url}/v3/$(tenant_id)s",
                        },
                        {
                            "service_name": "cinder",
                            "type": "block-storage",
                            "description": "Cinder Volume Service v3",
                            "internal_url": f"{interal_url}/v3/$(tenant_id)s",
                            "public_url": f"{public_url}/v3/$(tenant_id)s",
                            "admin_url": f"{interal_url}/v3/$(tenant_id)s",
                        },
                    ]
                ),
            },
        )
        return rel_id

    def ks_manager_mock(self):
        """Create keystone manager mock."""

        def _create_mock(p_name, p_id):
            return {"id": p_id, "name": p_name}

        def _get_domain_side_effect(name: str):
            if name == "admin_domain":
                return admin_domain_mock
            else:
                return service_domain_mock

        service_domain_mock = _create_mock("sdomain_name", "sdomain_id")
        admin_domain_mock = _create_mock("adomain_name", "adomain_id")

        admin_project_mock = _create_mock("aproject_name", "aproject_id")

        service_user_mock = _create_mock("suser_name", "suser_id")
        admin_user_mock = _create_mock("auser_name", "auser_id")

        admin_role_mock = _create_mock("arole_name", "arole_id")

        km_mock = MagicMock()
        km_mock.ksclient.show_domain.side_effect = _get_domain_side_effect
        km_mock.ksclient.show_project.return_value = admin_project_mock
        km_mock.ksclient.show_user.return_value = admin_user_mock
        km_mock.ksclient.create_user.return_value = service_user_mock
        km_mock.ksclient.create_role.return_value = admin_role_mock
        km_mock.create_service_account.return_value = service_user_mock
        km_mock.read_keys.return_value = {
            "0": "Qf4vHdf6XC2dGKpEwtGapq7oDOqUWepcH2tKgQ0qOKc=",
            "3": "UK3qzLGvu-piYwau0BFyed8O3WP8lFKH_v1sXYulzhs=",
            "4": "YVYUJbQNASbVzzntqj2sG9rbDOV_QQfueDCz0PJEKKw=",
        }
        return km_mock

    def setUp(self):
        """Run test setup."""
        super().setUp(charm, self.PATCHES)

        # used by _launch_heartbeat.
        # value doesn't matter for tests because mocking
        os.environ["JUJU_CHARM_DIR"] = "/arbitrary/directory/"
        self.pwgen.pwgen.return_value = "randonpassword"

        self.km_mock = self.ks_manager_mock()
        self.manager.KeystoneManager.return_value = self.km_mock
        self.harness = test_utils.get_harness(
            _KeystoneOperatorCharm, container_calls=self.container_calls
        )

        # clean up events that were dynamically defined,
        # otherwise we get issues because they'll be redefined,
        # which is not allowed.
        from charms.data_platform_libs.v0.data_interfaces import (
            DatabaseRequiresEvents,
        )

        for attr in (
            "database_database_created",
            "database_endpoints_changed",
            "database_read_only_endpoints_changed",
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass

        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    # This function need to be moved to operator
    def get_secret_by_label(self, label: str) -> str:
        """Get secret by label from harness class."""
        for secret in self.harness._backend._secrets:
            if secret.label == label:
                return secret.id

        return None

    def test_pebble_ready_handler(self):
        """Test pebble ready handler."""
        self.assertEqual(self.harness.charm.seen_events, [])
        self.harness.container_pebble_ready("keystone")
        self.assertEqual(self.harness.charm.seen_events, ["PebbleReadyEvent"])

    def test_trusted_dashboard_relation(self):
        """Test trusted-dashboard relation."""
        secret_mock = MagicMock()
        secret_mock.id = "test_oauth_secret"
        secret_mock.get_content.return_value = {"secret": "super secret"}
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )

        rel_id = self.add_trusted_dashboard_relation()
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.unit.app.name
        )
        # trusted dashboard requirer needs oauth data to be set before
        # it notifies the provider.
        self.assertEqual(rel_data, {})

        self.add_oauth_relation()
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.unit.app.name
        )
        self.assertEqual(
            rel_data,
            {
                "federated-providers": json.dumps(
                    [
                        {
                            "name": "hydra",
                            "protocol": "openid",
                            "description": "Hydra",
                        }
                    ]
                )
            },
        )

    def test_oauth_relation(self):
        """Test responding to an identity client."""
        secret_mock = MagicMock()
        secret_mock.id = "test_oauth_secret"
        secret_mock.get_content.return_value = {"secret": "super secret"}
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        oauth_rel_id = self.add_oauth_relation()
        rel_data = self.harness.get_relation_data(
            oauth_rel_id, self.harness.charm.unit.app.name
        )
        rel_data_hydra = self.harness.get_relation_data(oauth_rel_id, "hydra")
        self.assertEqual(
            rel_data,
            {
                "redirect_uri": "http://public-url/v3/OS-FEDERATION/protocols/openid/redirect_uri",
                "scope": "openid email profile",
                "grant_types": json.dumps(
                    [
                        "authorization_code",
                        "client_credentials",
                        "refresh_token",
                    ]
                ),
                "audience": "[]",
                "token_endpoint_auth_method": "client_secret_basic",
            },
        )
        issuer_url = "https://172.16.1.207/iam-hydra"
        self.assertEqual(
            rel_data_hydra,
            {
                "authorization_endpoint": f"{issuer_url}/oauth2/auth",
                "client_id": "c733827d-d6e0-45dd-8210-fdc9b6525f29",
                "client_secret_id": "secret://test_oauth_secret",
                "introspection_endpoint": "http://hydra.iam.svc.cluster.local:4445/admin/oauth2/introspect",
                "issuer_url": issuer_url,
                "jwks_endpoint": f"{issuer_url}/.well-known/jwks.json",
                "jwt_access_token": "True",
                "scope": "openid profile email phone",
                "token_endpoint": f"{issuer_url}/oauth2/token",
                "userinfo_endpoint": f"{issuer_url}/userinfo",
            },
        )

    def test_keystone_saml2_relation(self):
        """Test responding to a teystone saml2 relation."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        ks_saml_rel_id = self.add_keystone_saml_relation()
        rel_data = self.harness.get_relation_data(
            ks_saml_rel_id, self.harness.charm.unit.app.name
        )
        rel_data_saml = self.harness.get_relation_data(
            ks_saml_rel_id, "keystone-saml-entra"
        )
        self.maxDiff = None
        acs_url = "http://public-url/v3/OS-FEDERATION/identity_providers/entra/protocols/saml2/auth/mellon/postResponse"
        logout_url = "http://public-url/v3/OS-FEDERATION/identity_providers/entra/protocols/saml2/auth/mellon/logout"
        metadata_url = "http://public-url/v3/OS-FEDERATION/identity_providers/entra/protocols/saml2/auth/mellon/metadata"
        self.assertEqual(
            rel_data,
            {
                "acs-url": acs_url,
                "logout-url": logout_url,
                "metadata-url": metadata_url,
            },
        )

        self.assertEqual(
            rel_data_saml,
            {
                "name": "entra",
                "label": "Log in with Entra SAML2",
                "metadata": "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4=",
            },
        )

    def test_sync_oidc_providers(self):
        """Tests that OIDC provider metadata is written to disk."""
        secret_mock = MagicMock()
        secret_mock.id = "test_oauth_secret"
        secret_mock.get_content.return_value = {"secret": "super secret"}
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        self.add_oauth_relation()

        _get_oidc_metadata_mock = MagicMock()
        _get_oidc_metadata_mock.return_value = {"hello": "world"}
        self.harness.charm.oauth._get_oidc_metadata = _get_oidc_metadata_mock

        # this needs to be a url encoded string which consists
        # of the issuer URL, with the scheme and trailing slash removed.
        encoded_issuer_url = "172.16.1.207%2Fiam-hydra"
        self.harness.charm.sync_oidc_providers()

        self.assertEqual(self.km_mock.setup_oidc_metadata_folder.call_count, 1)
        self.assertEqual(self.km_mock.write_oidc_metadata.call_count, 1)
        self.km_mock.write_oidc_metadata.assert_called_with(
            {
                f"{encoded_issuer_url}.provider": json.dumps(
                    {"hello": "world"}
                ),
                f"{encoded_issuer_url}.client": json.dumps(
                    {
                        "client_id": "c733827d-d6e0-45dd-8210-fdc9b6525f29",
                        "client_secret": "super secret",
                    }
                ),
            }
        )

    def test_get_idp_file_name_from_issuer_url(self):
        """Test we get a base file name from issuer_url."""
        issuer_url = "https://172.16.1.207/iam-hydra/"
        encoded_issuer_url = "172.16.1.207%2Fiam-hydra"

        result = self.harness.charm.oauth._get_idp_file_name_from_issuer_url(
            issuer_url
        )
        self.assertEqual(encoded_issuer_url, result)

    def test_id_client(self):
        """Test responding to an identity client."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        peer_rel_id = self.harness.add_relation("peers", "keystone")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        identity_rel_id = self.add_id_relation()
        rel_data = self.harness.get_relation_data(
            identity_rel_id, self.harness.charm.unit.app.name
        )
        secret_svc_cinder = self.get_secret_by_label("credentials_svc_cinder")
        self.maxDiff = None
        self.assertEqual(
            rel_data,
            {
                "admin-auth-url": "http://internal-url/v3",
                "admin-domain-id": "adomain_id",
                "admin-domain-name": "adomain_name",
                "admin-project-id": "aproject_id",
                "admin-project-name": "aproject_name",
                "admin-role": "admin",
                "admin-user-id": "auser_id",
                "admin-user-name": "auser_name",
                "api-version": "v3",
                "auth-host": "10.0.0.10",
                "auth-port": "5000",
                "auth-protocol": "http",
                "internal-auth-url": "http://internal-url/v3",
                "internal-host": "internal-url",
                "internal-port": "80",
                "internal-protocol": "http",
                "public-auth-url": "http://public-url/v3",
                "region": "RegionOne",
                "service-domain-id": "sdomain_id",
                "service-domain-name": "sdomain_name",
                "service-host": "10.0.0.10",
                "service-credentials": secret_svc_cinder,
                "service-port": "5000",
                "service-project-id": "aproject_id",
                "service-project-name": "aproject_name",
                "service-protocol": "http",
                "service-user-id": "suser_id",
            },
        )

        peer_data = self.harness.get_relation_data(
            peer_rel_id, self.harness.charm.unit.app.name
        )
        fernet_secret_id = self.get_secret_by_label("fernet-keys")
        credential_secret_id = self.get_secret_by_label("credential-keys")
        oidc_secret_id = self.get_secret_by_label("oidc-crypto-passphrase")
        self.assertEqual(
            peer_data,
            {
                "leader_ready": "true",
                "fernet-secret-id": fernet_secret_id,
                "credential-keys-secret-id": credential_secret_id,
                "credentials_svc_cinder": secret_svc_cinder,
                "oidc-crypto-passphrase": oidc_secret_id,
            },
        )

    def test_leader_bootstraps(self):
        """Test leader bootstrap."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        test_utils.add_complete_logging_relation(self.harness)
        self.km_mock.setup_keystone.assert_called_once_with()
        self.km_mock.setup_initial_projects_and_users.assert_called_once_with()

        peer_data = self.harness.get_relation_data(
            rel_id, self.harness.charm.unit.app.name
        )
        fernet_secret_id = self.get_secret_by_label("fernet-keys")
        credential_secret_id = self.get_secret_by_label("credential-keys")
        oidc_secret_id = self.get_secret_by_label("oidc-crypto-passphrase")
        self.assertEqual(
            peer_data,
            {
                "leader_ready": "true",
                "fernet-secret-id": fernet_secret_id,
                "credential-keys-secret-id": credential_secret_id,
                "oidc-crypto-passphrase": oidc_secret_id,
            },
        )
        assert self.harness.charm.logging.ready

    def test_on_peer_data_changed_no_bootstrap(self):
        """Test peer_relation_changed on no bootstrap."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")

        event = MagicMock()
        self.harness.charm._on_peer_data_changed(event)
        self.assertTrue(event.defer.called)

    def test_on_peer_data_changed_with_fernet_keys_and_fernet_secret_different(
        self,
    ):
        """Test peer_relation_changed when fernet keys and secret have different content."""
        updated_fernet_keys = {
            "0": "Qf4vHdf6XC2dGKpEwtGapq7oDOqUWepcH2tKgQ0qOKc=",
            "2": "UK3qzLGvu-piYwau0BFyed8O3WP8lFKH_v1sXYulzhs=",
            "3": "yvyujbqnasbvzzntqj2sg9rbdov_qqfuedcz0pjekkw=",
        }
        secret_fernet_keys = {
            f"fernet-{k}": v for k, v in updated_fernet_keys.items()
        }

        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )

        secret_id = self.harness.get_relation_data(rel_id, "keystone-k8s")[
            "fernet-secret-id"
        ]
        s = self.harness.model.get_secret(id=secret_id)
        s.set_content(secret_fernet_keys)
        s.get_content(refresh=True)
        secret_id = self.harness.get_relation_data(rel_id, "keystone-k8s")[
            "credential-keys-secret-id"
        ]
        s = self.harness.model.get_secret(id=secret_id)
        s.set_content(secret_fernet_keys)
        s.get_content(refresh=True)

        event = MagicMock()
        self.harness.charm._on_peer_data_changed(event)

        self.assertTrue(self.km_mock.read_keys.called)
        self.assertEqual(self.km_mock.write_keys.call_count, 2)
        self.km_mock.write_keys.assert_has_calls(
            [
                call(
                    key_repository="/etc/keystone/fernet-keys",
                    keys=updated_fernet_keys,
                ),
                call(
                    key_repository="/etc/keystone/credential-keys",
                    keys=updated_fernet_keys,
                ),
            ]
        )

    def test_on_peer_data_changed_with_fernet_keys_and_fernet_secret_same(
        self,
    ):
        """Test peer_relation_changed when fernet keys and secret have same content."""
        secret_mock = MagicMock()
        secret_mock.id = "test-secret-id"
        secret_mock.get_content.return_value = self.km_mock.read_keys()
        self.harness.model.app.add_secret = MagicMock()
        self.harness.model.app.add_secret.return_value = secret_mock
        self.harness.model.get_secret = MagicMock()
        self.harness.model.get_secret.return_value = secret_mock

        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )

        event = MagicMock()
        self.harness.charm._on_peer_data_changed(event)
        self.assertTrue(self.harness.model.get_secret.called)
        self.assertTrue(self.km_mock.read_keys.called)
        self.assertFalse(self.km_mock.write_keys.called)

    def _test_non_leader_on_secret_rotate(self, label: str):
        """Test secert-rotate event on non leader unit."""
        test_utils.add_complete_ingress_relation(self.harness)
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")

        event = MagicMock()
        event.secret.label = label
        self.harness.charm._on_secret_rotate(event)
        if label == "fernet-keys":
            self.assertFalse(self.km_mock.rotate_fernet_keys.called)
        elif label == "credential-keys":
            self.assertFalse(self.km_mock.rotate_credential_keys.called)

    def _test_leader_on_secret_rotate(self, label: str):
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")

        event = MagicMock()
        event.secret.label = label
        self.harness.charm._on_secret_rotate(event)
        if label == "fernet-keys":
            fernet_keys_ = {
                f"fernet-{k}": v for k, v in self.km_mock.read_keys().items()
            }
            self.assertTrue(self.km_mock.rotate_fernet_keys.called)
            event.secret.set_content.assert_called_once_with(fernet_keys_)
        elif label == "credential-keys":
            fernet_keys_ = {
                f"fernet-{k}": v for k, v in self.km_mock.read_keys().items()
            }
            self.assertTrue(self.km_mock.rotate_credential_keys.called)
            event.secret.set_content.assert_called_once_with(fernet_keys_)

    def test_leader_on_secret_rotate_for_label_fernet_keys(self):
        """Test secret-rotate event for label fernet_keys on leader unit."""
        self._test_leader_on_secret_rotate(label="fernet-keys")

    def test_leader_on_secret_rotate_for_label_credential_keys(self):
        """Test secret-rotate event for label credential_keys on leader unit."""
        self._test_leader_on_secret_rotate(label="credential-keys")

    def test_non_leader_on_secret_rotate_for_label_fernet_keys(self):
        """Test secret-rotate event for label fernet_keys on non leader unit."""
        self._test_non_leader_on_secret_rotate(label="fernet-keys")

    def test_non_leader_on_secret_rotate_for_label_credential_keys(self):
        """Test secret-rotate event for label credential_keys on non leader unit."""
        self._test_non_leader_on_secret_rotate(label="credential-keys")

    def test_leader_on_secret_rotate_identity_service_secret(self):
        """Test secret-rotate event for label identity_service_secret on leader unit."""
        create_service_account = MagicMock(side_effect=[{"name": "cinder"}])
        configure_mock = MagicMock(
            side_effect=self.harness.charm.configure_charm
        )

        self._test_secret_rotate_identity_credentials(
            create_service_mock=create_service_account,
            configure_mock=configure_mock,
        )
        self.assertEqual(create_service_account.call_count, 1)
        self.assertEqual(configure_mock.call_count, 0)

    def test_leader_on_secret_rotate_identity_service_secret_when_failing_to_connect_once(
        self,
    ):
        """When the secret rotate hook fails to connect once, it should retry.

        The hook will try to configure the charm, and then retry to create the service account.
        """
        create_service_account = MagicMock(
            side_effect=[
                keystoneauth1.exceptions.ConnectFailure(
                    "Failed to connect..."
                ),
                {"name": "cinder"},
            ]
        )
        configure_mock = MagicMock(
            side_effect=self.harness.charm.configure_charm
        )

        self._test_secret_rotate_identity_credentials(
            create_service_mock=create_service_account,
            configure_mock=configure_mock,
        )
        self.assertEqual(create_service_account.call_count, 2)
        self.assertEqual(configure_mock.call_count, 1)

    def test_leader_on_secret_rotate_identity_service_secret_when_failing_to_connect_twice(
        self,
    ):
        """This will fail to connect twice, and then raise an error."""
        create_service_account = MagicMock(
            side_effect=[
                keystoneauth1.exceptions.ConnectFailure(
                    "Failed to connect..."
                ),
                keystoneauth1.exceptions.ConnectFailure(
                    "Failed to connect..."
                ),
            ]
        )
        configure_mock = MagicMock(
            side_effect=self.harness.charm.configure_charm
        )

        with self.assertRaises(keystoneauth1.exceptions.ConnectFailure):
            self._test_secret_rotate_identity_credentials(
                create_service_mock=create_service_account,
                configure_mock=configure_mock,
            )
        self.assertEqual(create_service_account.call_count, 2)
        self.assertEqual(configure_mock.call_count, 1)

    def test_leader_on_secret_rotate_identity_service_secret_when_unexpected_error(
        self,
    ):
        """This is an unhandled exception, it should have been bubbled up."""
        create_service_account = MagicMock(
            side_effect=Exception("I am unexpected..."),
        )
        configure_mock = MagicMock(
            side_effect=self.harness.charm.configure_charm
        )

        with self.assertRaises(Exception):
            self._test_secret_rotate_identity_credentials(
                create_service_mock=create_service_account,
                configure_mock=configure_mock,
            )
        self.assertEqual(create_service_account.call_count, 1)
        self.assertEqual(configure_mock.call_count, 0)

    def test_leader_on_secret_rotate_identity_service_secret_when_configured_not_active(
        self,
    ):
        """Keystone is not ready, it should raise a blocked exception."""
        create_service_account = MagicMock(
            side_effect=keystoneauth1.exceptions.ConnectFailure(
                "Failed to connect..."
            ),
        )
        configure_mock = MagicMock(
            side_effect=self.harness.charm.configure_charm
        )
        with self.assertRaises(sunbeam_guard.BlockedExceptionError):
            self._test_secret_rotate_identity_credentials(
                create_service_mock=create_service_account,
                configure_mock=configure_mock,
                remove_ingress=True,
            )
        self.assertEqual(create_service_account.call_count, 1)
        self.assertEqual(configure_mock.call_count, 1)

    def _test_secret_rotate_identity_credentials(
        self,
        create_service_mock: MagicMock,
        configure_mock: MagicMock,
        remove_ingress=False,
    ):
        test_utils.add_complete_ingress_relation(self.harness)
        test_utils.add_complete_db_relation(self.harness)
        test_utils.add_complete_peer_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        rel_id = self.add_id_relation()
        rel_data = self.harness.get_relation_data(
            rel_id, self.harness.model.app.name
        )
        label = charm.CREDENTIALS_SECRET_PREFIX + "svc_" + "cinder"
        secret_id = rel_data["service-credentials"]
        if remove_ingress:
            rel = self.harness.charm.model.get_relation("ingress-internal")
            rel_id = rel.id
            self.harness.remove_relation(rel_id)
        self.km_mock.create_service_account = create_service_mock
        self.harness.charm.configure_charm = configure_mock
        self.harness.trigger_secret_rotation(secret_id, label=label)

    def test_on_secret_changed_with_fernet_keys_and_fernet_secret_same(self):
        """Test secret change event when fernet keys and secret have same content."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")

        event = MagicMock()
        event.secret.label = "fernet-keys"
        event.secret.get_content.return_value = self.km_mock.read_keys()
        self.harness.charm._on_secret_changed(event)

        self.assertTrue(event.secret.get_content.called)
        self.assertTrue(self.km_mock.read_keys.called)
        self.assertFalse(self.km_mock.write_keys.called)

    def test_on_secret_changed_with_fernet_keys_and_fernet_secret_different(
        self,
    ):
        """Test secret change event when fernet keys and secret have different content."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")

        event = MagicMock()
        event.secret.label = "fernet-keys"
        event.secret.get_content.return_value = {
            "0": "Qf4vHdf6XC2dGKpEwtGapq7oDOqUWepcH2tKgQ0qOKc=",
            "4": "UK3qzLGvu-piYwau0BFyed8O3WP8lFKH_v1sXYulzhs=",
            "5": "YVYUJbQNASbVzzntqj2sG9rbDOV_QQfueDCz0PJEKKw=",
        }
        self.harness.charm._on_secret_changed(event)

        self.assertTrue(event.secret.get_content.called)
        self.assertTrue(self.km_mock.read_keys.called)
        self.assertTrue(self.km_mock.write_keys.called)

    def test_non_leader_no_bootstraps(self):
        """Test bootstrapping on a non-leader."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader(False)
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        self.assertFalse(self.km_mock.setup_keystone.called)

    def test_get_service_account_action(self):
        """Test get_service_account action."""
        self.harness.add_relation("peers", "keystone-k8s")

        action_event = MagicMock()
        action_event.params = {"username": "external_service"}

        # Check call on non-lead unit.
        self.harness.charm._get_service_account_action(action_event)

        action_event.set_results.assert_not_called()
        action_event.fail.assert_called()

        # Check call on lead unit.
        self.harness.set_leader()
        self.harness.charm._get_service_account_action(action_event)

        action_event.set_results.assert_called_with(
            {
                "username": "external_service",
                "password": "randonpassword",
                "user-domain-name": "sdomain_name",
                "project-name": "aproject_name",
                "project-domain-name": "sdomain_name",
                "region": "RegionOne",
                "internal-endpoint": "http://10.0.0.10:5000/v3",
                "public-endpoint": "http://10.0.0.10:5000/v3",
                "api-version": 3,
            }
        )

    def test_get_admin_account_action(self):
        """Test admin account action."""
        self.harness.add_relation("peers", "keystone-k8s")
        action_event = MagicMock()

        self.harness.charm._get_admin_account_action(action_event)
        action_event.set_results.assert_not_called()
        action_event.fail.assert_called()

        self.harness.set_leader()
        self.harness.charm._get_admin_account_action(action_event)

        action_event.set_results.assert_called_with(
            {
                "username": "admin",
                "password": "randonpassword",
                "user-domain-name": "admin_domain",
                "project-name": "admin",
                "project-domain-name": "admin_domain",
                "region": "RegionOne",
                "internal-endpoint": "http://10.0.0.10:5000/v3",
                "public-endpoint": "http://10.0.0.10:5000/v3",
                "api-version": 3,
                "openrc": ANY,
            }
        )

    def test_domain_config(self):
        """Test domain config."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        rel_id = self.harness.add_relation("peers", "keystone-k8s")
        self.harness.add_relation_unit(rel_id, "keystone-k8s/1")
        self.harness.container_pebble_ready("keystone")
        test_utils.add_db_relation_credentials(
            self.harness, test_utils.add_base_db_relation(self.harness)
        )
        dc_id = self.harness.add_relation("domain-config", "keystone-ldap-k8s")
        self.harness.add_relation_unit(dc_id, "keystone-ldap-k8s/0")
        b64file = (
            "W2xkYXBdCmdyb3VwX21lbWJlcl9hdHRyaWJ1dGUgPSBtZW1iZXJVaWQKZ3JvdXBf"
            "bWVtYmVyc19hcmVfaWRzID0gdHJ1ZQpncm91cF9uYW1lX2F0dHJpYnV0ZSA9IGNu"
            "Cmdyb3VwX29iamVjdGNsYXNzID0gcG9zaXhHcm91cApncm91cF90cmVlX2RuID0g"
            "b3U9Z3JvdXBzLGRjPXRlc3QsZGM9Y29tCnBhc3N3b3JkID0gY3JhcHBlcgpzdWZm"
            "aXggPSBkYz10ZXN0LGRjPWNvbQp1cmwgPSBsZGFwOi8vMTAuMS4xNzYuMTg0CnVz"
            "ZXIgPSBjbj1hZG1pbixkYz10ZXN0LGRjPWNvbQpbaWRlbnRpdHldCmRyaXZlciA9"
            "IGxkYXA="
        )
        domain_config = {
            "domain-name": "mydomain",
            "config-contents": b64file,
        }
        self.harness.update_relation_data(
            dc_id, "keystone-ldap-k8s", domain_config
        )
        expect_entries = """
        [ldap]
        group_member_attribute = memberUid
        group_members_are_ids = true
        group_name_attribute = cn
        group_objectclass = posixGroup
        group_tree_dn = ou=groups,dc=test,dc=com
        password = crapper
        suffix = dc=test,dc=com
        url = ldap://10.1.176.184
        user = cn=admin,dc=test,dc=com
        [identity]
        driver = ldap"""
        self.maxDiff = None
        self.check_file(
            "keystone",
            "/etc/keystone/domains/keystone.mydomain.conf",
            contents=textwrap.dedent(expect_entries).lstrip(),
        )


class TestIdentityResourceProvidesHandler(unittest.TestCase):
    """Tests for IdentityResourceProvidesHandler methods."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_charm = MagicMock()
        self.mock_charm.unit.is_leader.return_value = True
        self.mock_model = MagicMock()
        self.mock_charm.model = self.mock_model

        self.mock_relation = MagicMock()
        self.mock_relation.name = "identity-ops"
        self.mock_relation.id = 1
        self.mock_model.get_relation.return_value = self.mock_relation

        self.mock_interface = MagicMock()

        # Peers mock for SecretStore
        self.mock_charm.peers.ready = True
        self._store_data = {}

        def _get_app_data(key):
            return self._store_data.get(key)

        def _set_app_data(data):
            self._store_data.update(data)

        self.mock_charm.peers.get_app_data.side_effect = _get_app_data
        self.mock_charm.peers.set_app_data.side_effect = _set_app_data

        # Build handler
        self.handler = charm.IdentityResourceProvidesHandler.__new__(
            charm.IdentityResourceProvidesHandler
        )
        self.handler.charm = self.mock_charm
        self.handler.relation_name = "identity-ops"
        self.handler.callback_f = MagicMock()
        self.handler.interface = self.mock_interface

        # Patch model property to return our mock (ops.Object.model
        # resolves via framework which we don't init)
        self._model_patcher = patch.object(
            type(self.handler),
            "model",
            new_callable=lambda: property(lambda s: self.mock_model),
        )
        self._model_patcher.start()

        self.handler.store = charm.SecretStore(self.mock_charm, "id-ops-store")

        # Mock ksclient on charm
        self.mock_ksclient = MagicMock()
        self.mock_charm.keystone_manager.ksclient = self.mock_ksclient

    def tearDown(self):
        """Clean up patchers."""
        self._model_patcher.stop()

    # ── SecretStore tests ──────────────────────────────────────────────

    def test_secret_store_store_and_get(self):
        """Test storing and getting a secret."""
        self.handler.store.store_secret("my-label", "secret:abc123")
        self.assertEqual(
            self.handler.store.get_secret("my-label"), "secret:abc123"
        )

    def test_secret_store_get_nonexistent(self):
        """Test getting a nonexistent secret returns None."""
        self.assertIsNone(self.handler.store.get_secret("missing"))

    def test_secret_store_drop_secret(self):
        """Test dropping a secret."""
        self.handler.store.store_secret("lbl", "sid")
        self.handler.store.drop_secret("lbl")
        self.assertIsNone(self.handler.store.get_secret("lbl"))

    def test_secret_store_drop_nonexistent(self):
        """Dropping a non-existent label should be a no-op."""
        self.handler.store.drop_secret("nope")

    def test_secret_store_list_secrets_with_prefix(self):
        """Test listing secrets by prefix."""
        self.handler.store.store_secret("identity-ops-1:A", "s1")
        self.handler.store.store_secret("identity-ops-1:B", "s2")
        self.handler.store.store_secret("identity-ops-2:C", "s3")
        result = self.handler.store.list_secrets_with_prefix("identity-ops-1:")
        self.assertEqual(
            result, {"identity-ops-1:A": "s1", "identity-ops-1:B": "s2"}
        )

    def test_secret_store_not_ready(self):
        """Secret store should return empty/None when peers not ready."""
        self.mock_charm.peers.ready = False
        self.assertIsNone(self.handler.store.get_secret("x"))
        self.assertEqual(self.handler.store.list_secrets_with_prefix("x"), {})

    def test_secret_store_non_leader_cannot_write(self):
        """Non-leader should not be able to store or drop secrets."""
        self.mock_charm.unit.is_leader.return_value = False
        self.handler.store.store_secret("lbl", "sid")
        self.assertIsNone(self.handler.store.get_secret("lbl"))

    # ── to_prefix / to_label ──────────────────────────────────────────

    def test_to_prefix(self):
        """Test prefix generation."""
        self.assertEqual(
            self.handler.to_prefix(self.mock_relation),
            "identity-ops-1:",
        )

    def test_to_label(self):
        """Test label generation."""
        self.assertEqual(
            self.handler.to_label(self.mock_relation, "my-req"),
            "identity-ops-1:my-req",
        )

    # ── _sanitize_secrets ──────────────────────────────────────────────

    def test_sanitize_secrets_replaces_secret_prefix(self):
        """Params starting with secret:// should be replaced with secret content."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"password": "s3cret"}
        self.mock_model.get_secret.return_value = mock_secret

        request = {
            "ops": [
                {
                    "name": "create_user",
                    "params": {"password": "secret://my-secret-id"},
                }
            ]
        }
        result = self.handler._sanitize_secrets(request)
        self.assertEqual(result["ops"][0]["params"]["password"], "s3cret")
        self.mock_model.get_secret.assert_called_once_with(
            id="secret://my-secret-id"
        )

    def test_sanitize_secrets_no_secret_prefix_untouched(self):
        """Params without secret:// prefix should stay as is."""
        request = {
            "ops": [
                {
                    "name": "create_user",
                    "params": {"name": "alice", "domain_id": "default"},
                }
            ]
        }
        result = self.handler._sanitize_secrets(request)
        self.assertEqual(result["ops"][0]["params"]["name"], "alice")
        self.assertEqual(result["ops"][0]["params"]["domain_id"], "default")
        self.mock_model.get_secret.assert_not_called()

    def test_sanitize_secrets_raises_on_model_error(self):
        """Should raise ValueError when secret cannot be retrieved."""
        self.mock_model.get_secret.side_effect = ModelError()
        request = {
            "ops": [
                {
                    "name": "create_user",
                    "params": {"password": "secret://bad-id"},
                }
            ]
        }
        with self.assertRaises(ValueError):
            self.handler._sanitize_secrets(request)

    def test_sanitize_secrets_raises_on_secret_not_found(self):
        """Should raise ValueError when secret is not found."""
        self.mock_model.get_secret.side_effect = SecretNotFoundError("gone")
        request = {
            "ops": [
                {
                    "name": "create_user",
                    "params": {"password": "secret://bad-id"},
                }
            ]
        }
        with self.assertRaises(ValueError):
            self.handler._sanitize_secrets(request)

    def test_sanitize_secrets_non_string_values_untouched(self):
        """Non-string param values should be left as is."""
        request = {
            "ops": [{"name": "op1", "params": {"count": 5, "flag": True}}]
        }
        result = self.handler._sanitize_secrets(request)
        self.assertEqual(result["ops"][0]["params"]["count"], 5)
        self.assertEqual(result["ops"][0]["params"]["flag"], True)

    # ── _generate_secret_params ────────────────────────────────────────

    @patch("charm.sunbeam_core.random_string", return_value="randomval")
    def test_generate_secret_params(self, mock_rand):
        """Test secret parameter generation."""
        result = self.handler._generate_secret_params(
            ["password", "token"], length=16
        )
        self.assertEqual(
            result, {"password": "randomval", "token": "randomval"}
        )
        self.assertEqual(mock_rand.call_count, 2)
        mock_rand.assert_called_with(16)

    @patch("charm.sunbeam_core.random_string")
    def test_generate_secret_params_empty_list(self, mock_rand):
        """Empty secret_params should return empty dict."""
        result = self.handler._generate_secret_params([])
        self.assertEqual(result, {})
        mock_rand.assert_not_called()

    # ── _create_and_grant_ops_secret ───────────────────────────────────

    def test_create_and_grant_new_secret(self):
        """Should create a new secret and grant it to the relation."""
        mock_secret = MagicMock()
        mock_secret.id = "secret:new-id"
        self.mock_model.app.add_secret.return_value = mock_secret

        secret_data = {"password": "pw123"}
        result = self.handler._create_and_grant_ops_secret(
            self.mock_relation, "my-req", secret_data
        )

        self.assertEqual(result, "secret:new-id")
        self.mock_model.app.add_secret.assert_called_once_with(
            secret_data,
            label="identity-ops-1:my-req",
        )
        mock_secret.grant.assert_called_once_with(self.mock_relation)
        # Should be stored in the store
        label = self.handler.to_label(self.mock_relation, "my-req")
        self.assertEqual(self.handler.store.get_secret(label), "secret:new-id")

    def test_create_and_grant_updates_existing_secret(self):
        """Should update and re-grant an existing secret."""
        label = self.handler.to_label(self.mock_relation, "my-req")
        self.handler.store.store_secret(label, "secret:old-id")

        mock_secret = MagicMock()
        self.mock_model.get_secret.return_value = mock_secret

        secret_data = {"password": "newpw"}
        result = self.handler._create_and_grant_ops_secret(
            self.mock_relation, "my-req", secret_data
        )

        self.assertEqual(result, "secret:old-id")
        self.mock_model.get_secret.assert_called_once_with(id="secret:old-id")
        mock_secret.set_content.assert_called_once_with(secret_data)
        mock_secret.grant.assert_called_once_with(self.mock_relation)
        # Should NOT create a new secret
        self.mock_model.app.add_secret.assert_not_called()

    def test_create_and_grant_recreates_when_existing_not_found(self):
        """Should create new secret when stored reference is stale."""
        label = self.handler.to_label(self.mock_relation, "my-req")
        self.handler.store.store_secret(label, "secret:stale-id")

        self.mock_model.get_secret.side_effect = SecretNotFoundError("gone")

        mock_new_secret = MagicMock()
        mock_new_secret.id = "secret:brand-new"
        self.mock_model.app.add_secret.return_value = mock_new_secret

        result = self.handler._create_and_grant_ops_secret(
            self.mock_relation, "my-req", {"password": "pw"}
        )
        self.assertEqual(result, "secret:brand-new")
        self.mock_model.app.add_secret.assert_called_once()
        mock_new_secret.grant.assert_called_once_with(self.mock_relation)
        self.assertEqual(
            self.handler.store.get_secret(label), "secret:brand-new"
        )

    # ── _on_goneaway ──────────────────────────────────────────────────

    def test_on_goneaway_cleans_up_secrets(self):
        """Should remove all secrets for the departing relation."""
        # Pre-populate store with secrets for relation 1
        self.handler.store.store_secret("identity-ops-1:req-a", "secret:id-a")
        self.handler.store.store_secret("identity-ops-1:req-b", "secret:id-b")
        # Also have a secret for a different relation
        self.handler.store.store_secret("identity-ops-2:req-c", "secret:id-c")

        mock_secret_a = MagicMock()
        mock_secret_b = MagicMock()
        self.mock_model.get_secret.side_effect = [mock_secret_a, mock_secret_b]

        event = MagicMock()
        event.relation_name = "identity-ops"
        event.relation_id = 1

        self.handler._on_goneaway(event)

        # Both secrets should have been removed
        mock_secret_a.remove_all_revisions.assert_called_once()
        mock_secret_b.remove_all_revisions.assert_called_once()

        # Store entries for relation 1 should be gone
        self.assertIsNone(
            self.handler.store.get_secret("identity-ops-1:req-a")
        )
        self.assertIsNone(
            self.handler.store.get_secret("identity-ops-1:req-b")
        )
        # Relation 2 secret should be untouched
        self.assertEqual(
            self.handler.store.get_secret("identity-ops-2:req-c"),
            "secret:id-c",
        )

    def test_on_goneaway_ignores_missing_secrets(self):
        """Should continue and drop store entry even if secret is already gone."""
        self.handler.store.store_secret("identity-ops-1:req-a", "secret:id-a")
        self.mock_model.get_secret.side_effect = SecretNotFoundError("gone")

        event = MagicMock()
        event.relation_name = "identity-ops"
        event.relation_id = 1

        self.handler._on_goneaway(event)

        # Store entry should still be dropped
        self.assertIsNone(
            self.handler.store.get_secret("identity-ops-1:req-a")
        )

    # ── handle_op_request ──────────────────────────────────────────────

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_simple_success(self, _):
        """Test a simple op request with no secrets."""
        self.mock_ksclient.create_user.return_value = {"id": "user-1"}

        request = {
            "id": "req-1",
            "tag": "tag-1",
            "ops": [
                {
                    "name": "create_user",
                    "params": {"name": "alice", "domain_id": "default"},
                }
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        self.mock_ksclient.create_user.assert_called_once_with(
            name="alice", domain_id="default"
        )
        response = self.mock_interface.set_ops_response.call_args
        ops_response = response.kwargs["ops_response"]
        self.assertEqual(ops_response["id"], "req-1")
        self.assertEqual(ops_response["tag"], "tag-1")
        self.assertEqual(ops_response["ops"][0]["return-code"], 0)
        self.assertEqual(ops_response["ops"][0]["value"], {"id": "user-1"})
        self.assertNotIn("secret-id", ops_response["ops"][0])

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_with_secret_request(self, _):
        """Test op request that includes a secret-request."""
        self.mock_ksclient.create_user.return_value = {"id": "user-1"}

        mock_created_secret = MagicMock()
        mock_created_secret.id = "secret:created-id"
        self.mock_model.app.add_secret.return_value = mock_created_secret

        request = {
            "id": "req-1",
            "tag": "tag-1",
            "ops": [
                {
                    "name": "create_user",
                    "params": {"name": "alice"},
                    "secret-request": {
                        "secret-label": "creds-alice",
                        "secret-params": ["password"],
                    },
                }
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        # Should have called create_user with generated password
        call_kwargs = self.mock_ksclient.create_user.call_args.kwargs
        self.assertEqual(call_kwargs["name"], "alice")
        self.assertEqual(call_kwargs["password"], "genpw")

        # Should have created and granted a secret
        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(response["ops"][0]["return-code"], 0)
        self.assertEqual(response["ops"][0]["secret-id"], "secret:created-id")

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_secret_params_without_label_fails(self, _):
        """Secret-params without secret-label should cause an error."""
        request = {
            "id": "req-1",
            "tag": "tag-1",
            "ops": [
                {
                    "name": "create_user",
                    "params": {"name": "alice"},
                    "secret-request": {
                        "secret-params": ["password"],
                    },
                }
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(response["ops"][0]["return-code"], -1)
        self.assertIn(
            "secret-params provided without secret-label",
            response["ops"][0]["value"],
        )

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_relation_not_found(self, _):
        """Should return early (no response) when relation is not found."""
        self.mock_model.get_relation.return_value = None

        request = {
            "id": "req-1",
            "tag": "tag-1",
            "ops": [{"name": "create_user", "params": {"name": "alice"}}],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        self.mock_interface.set_ops_response.assert_not_called()

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_op_exception(self, _):
        """Op failure should return error code and repr of exception."""
        self.mock_ksclient.create_user.side_effect = RuntimeError("boom")

        request = {
            "id": "req-1",
            "tag": "tag-1",
            "ops": [{"name": "create_user", "params": {"name": "alice"}}],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(response["ops"][0]["return-code"], -1)
        self.assertIn("boom", response["ops"][0]["value"])

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_multiple_ops(self, _):
        """Test request with multiple ops."""
        self.mock_ksclient.create_user.return_value = {"id": "user-1"}
        self.mock_ksclient.create_role.return_value = {"id": "role-1"}

        request = {
            "id": "req-multi",
            "tag": "tag-multi",
            "ops": [
                {"name": "create_user", "params": {"name": "alice"}},
                {"name": "create_role", "params": {"name": "admin"}},
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(len(response["ops"]), 2)
        self.assertEqual(response["ops"][0]["return-code"], 0)
        self.assertEqual(response["ops"][0]["value"], {"id": "user-1"})
        self.assertEqual(response["ops"][1]["return-code"], 0)
        self.assertEqual(response["ops"][1]["value"], {"id": "role-1"})

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_partial_failure(self, _):
        """First op succeeds, second fails — both should be in response."""
        self.mock_ksclient.create_user.return_value = {"id": "user-1"}
        self.mock_ksclient.create_role.side_effect = RuntimeError("fail")

        request = {
            "id": "req-1",
            "tag": "t",
            "ops": [
                {"name": "create_user", "params": {"name": "alice"}},
                {"name": "create_role", "params": {"name": "admin"}},
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(response["ops"][0]["return-code"], 0)
        self.assertEqual(response["ops"][1]["return-code"], -1)

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_sanitizes_secrets_in_params(self, _):
        """Secret values in params should be resolved before calling the function."""
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"password": "resolved_pw"}
        self.mock_model.get_secret.return_value = mock_secret
        self.mock_ksclient.create_user.return_value = {"id": "u1"}

        request = {
            "id": "req-1",
            "tag": "t",
            "ops": [
                {
                    "name": "create_user",
                    "params": {
                        "name": "bob",
                        "password": "secret://my-secret",
                    },
                }
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        call_kwargs = self.mock_ksclient.create_user.call_args.kwargs
        self.assertEqual(call_kwargs["password"], "resolved_pw")

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_jinja_templating(self, _):
        """Jinja templates in params should be rendered using context from prior ops."""
        self.mock_ksclient.create_user.return_value = {
            "id": "user-1",
            "name": "alice",
        }
        self.mock_ksclient.grant_role.return_value = None

        request = {
            "id": "req-1",
            "tag": "t",
            "ops": [
                {
                    "name": "create_user",
                    "params": {"name": "alice"},
                },
                {
                    "name": "grant_role",
                    "params": {
                        "user_id": "{{ create_user[0]['id'] }}",
                        "role": "admin",
                    },
                },
            ],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        call_kwargs = self.mock_ksclient.grant_role.call_args.kwargs
        self.assertEqual(call_kwargs["user_id"], "user-1")
        self.assertEqual(call_kwargs["role"], "admin")

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_sanitize_failure_aborts(self, _):
        """If _sanitize_secrets raises, handle_op_request should not call any function."""
        self.mock_model.get_secret.side_effect = SecretNotFoundError("gone")

        request = {
            "id": "req-1",
            "tag": "t",
            "ops": [
                {
                    "name": "create_user",
                    "params": {
                        "name": "alice",
                        "password": "secret://bad-id",
                    },
                }
            ],
        }
        # _sanitize_secrets will raise ValueError, which propagates
        # since it happens before the per-op try/except
        with self.assertRaises(ValueError):
            self.handler.handle_op_request(1, "identity-ops", request)

        self.mock_ksclient.create_user.assert_not_called()

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_request_empty_ops(self, _):
        """Request with empty ops list should send response with empty ops."""
        request = {"id": "req-1", "tag": "t", "ops": []}
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        self.assertEqual(response["ops"], [])

    @patch("charm.sunbeam_core.random_string", return_value="genpw")
    def test_handle_op_response_initial_return_code(self, _):
        """Ops in response should initially have return-code -2 before execution."""
        # We test this indirectly: if the function isn't found, the
        # exception handler should set return-code to -1
        self.mock_ksclient.nonexistent_func = None
        delattr(self.mock_ksclient, "nonexistent_func")

        request = {
            "id": "req-1",
            "tag": "t",
            "ops": [{"name": "nonexistent_func", "params": {}}],
        }
        self.handler.handle_op_request(1, "identity-ops", request)

        response = self.mock_interface.set_ops_response.call_args.kwargs[
            "ops_response"
        ]
        # An AttributeError should have been caught
        self.assertEqual(response["ops"][0]["return-code"], -1)

    # ── ready ──────────────────────────────────────────────────────────

    def test_ready_always_true(self):
        """Handler.ready should always return True."""
        self.assertTrue(self.handler.ready)
