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
from unittest.mock import (
    ANY,
    MagicMock,
    call,
)

import charm
import keystoneauth1.exceptions
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.test_utils as test_utils


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

    def test_ingress_changed_catalog_connectfailure_is_handled(self):
        """Catalog update should handle keystone ConnectFailure."""
        test_utils.add_complete_ingress_relation(self.harness)
        self.harness.set_leader()
        self.harness.container_pebble_ready("keystone")
        self.harness.charm.bootstrapped = MagicMock(return_value=True)
        self.km_mock.update_service_catalog_for_keystone.side_effect = (
            keystoneauth1.exceptions.ConnectFailure("connect failed")
        )
        self.harness.charm._ingress_changed(MagicMock())
        self.km_mock.update_service_catalog_for_keystone.assert_called_once_with()

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
