# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import json
import yaml
import subprocess
import tempfile

from keystoneauth1.identity import v3
from keystoneauth1 import session
from keystoneclient.v3 import client as keystoneclient
import zaza
import zaza.model as model
from zaza.notifications import notify_around, NotifyEvents
import zaza.openstack.charm_tests.test_utils as test_utils
from zaza.openstack.utilities import openstack as openstack_utils


_FEDERATED_GROUP_NAME = "federated_users"
_FEDERATED_PROJECT_NAME = "federated_project"
_FEDERATED_DOMAIN_NAME = "canonical-iam"
_FEDERATED_IDENTITY_PROVIDER_NAME = "canonical-identity-platform"
_OPENID_PROTOCOL_NAME = "openid"
_IAM_MAPPING_RULES = """
[
    {
        "local": [
            {
                "user": {
                    "name": "{0}"
                },
                "group": {
                    "domain": {
                        "name": "%(domain_name)s"
                    },
                    "name": "%(group_name)s"
                }
            }
        ],
        "remote": [
            {
                "type": "REMOTE_USER"
            }
        ]
    }
]
"""


def _get_unit_rel_info(unit):
        command = ['juju', 'show-unit', '--format=json', unit]
        output = subprocess.check_output(command).decode()
        unit_info = json.loads(output)
        rel_info = unit_info.get(unit, {}).get("relation-info", [])
        return rel_info


def _get_issuer_url():
        issuer_url = None
        rel_info = _get_unit_rel_info("keystone/0")

        for rel in rel_info:
            if rel.get("endpoint") == "oauth" and rel.get("cross-model"):
                app_data = rel.get("application-data")
                if not app_data:
                    raise Exception(
                        "could not find application-data for "
                        "keystone oauth endpoint"
                    )
                issuer_url = app_data.get("issuer_url")
                break
        if not issuer_url:
            raise Exception("failed to find oauth issuer_url")
        return issuer_url


class SetupOffersAndRelations(object):
    application_name = "keystone"
    oauth_app_name = "hydra"
    oauth_cert_app_name = "self-signed-certificates"
    iam_saas_name = _FEDERATED_IDENTITY_PROVIDER_NAME
    iam_saas_cert_name = "iam-cert"
    openstack_model_name = "openstack"
    iam_model_name = "iam"

    def __init__(self):
        self.model_aliases = model.get_juju_model_aliases()
        iam_model = self.model_aliases.get(self.iam_model_name, None)
        if not iam_model:
            raise ValueError("could not get 'iam' model alias")
        self.model_name = self.model_aliases.get(
            self.openstack_model_name, None)
        if not self.model_name:
            raise ValueError("could not get 'openstack' model alias")

        self.iam_model = zaza.sync_wrapper(model.get_model)(
            model_name=iam_model)
        self.openstack_model = zaza.sync_wrapper(model.get_model)(
            model_name=self.model_name)
        self.openstack_model = zaza.sync_wrapper(model.get_model)(
            model_name=self.model_name)

        auth = openstack_utils.get_overcloud_auth()
        self.keystone_client = openstack_utils.get_keystone_client(auth)

    def _get_offer_url(self, application, endpoint):
        offers = zaza.sync_wrapper(
            self.iam_model.list_offers)()
        results = offers.get("results", [])
        for offer in results:
            if offer.application_name == application:
                for ep in offer.endpoints:
                    if ep.name == endpoint:
                        return offer.offer_url
        return ""

    def ensure_offers(self):
        self.oauth_offer_url = self._get_offer_url(
            self.oauth_app_name,
            "oauth",
        )
        self.oauth_cert_offer_url = self._get_offer_url(
            self.oauth_cert_app_name,
            "send-ca-cert",
        )
        if not self.oauth_offer_url:
            zaza.sync_wrapper(self.iam_model.create_offer)(
                f"{self.oauth_app_name}:oauth",
                application_name=self.oauth_app_name,
            )
            self.oauth_offer_url = self._get_offer_url(
                self.oauth_app_name,
                "oauth",
            )
        if not self.oauth_cert_offer_url:
            zaza.sync_wrapper(self.iam_model.create_offer)(
                f"{self.oauth_cert_app_name}:send-ca-cert",
                application_name=self.oauth_cert_app_name,
            )
            self.oauth_cert_offer_url = self._get_offer_url(
                self.oauth_cert_app_name,
                "send-ca-cert",
            )

    def _wait_for_settle(self):
        zaza.model.wait_for_agent_status(
            model_name=self.openstack_model.name
        )
        logging.info("Waiting for {} to settle".format(
            self.openstack_model_name))
        with notify_around(NotifyEvents.WAIT_MODEL_SETTLE,
                            model_name=self.openstack_model.name):
            zaza.model.block_until_all_units_idle(
                model_name=self.openstack_model.name)
        logging.info("Model {} has settled".format(
            self.openstack_model_name))

    def _get_role_by_name(self, domain, name):
        roles = self.keystone_client.roles.list(
            domain=domain,
            name=name,
        )
        if len(roles) == 0:
            raise ValueError("could not find role named %s", name)
        return roles[0].id

    def create_federated_domain(self):
        issuer_url = _get_issuer_url()
        domain = self.keystone_client.domains.create(
            name=_FEDERATED_DOMAIN_NAME,
            description="Domain used for federated users",
            enabled=True
        )
        project = self.keystone_client.projects.create(
            name=_FEDERATED_PROJECT_NAME,
            domain=domain,
            description="federated project",
            enabled=True,
        )
        group = self.keystone_client.groups.create(
            name=_FEDERATED_GROUP_NAME,
            domain=domain,
            description="federated users group",
        )
        self.keystone_client.roles.grant(
            role=self._get_role_by_name(domain, "member"),
            group=group,
            project=project,
        )
        rules = _IAM_MAPPING_RULES % {
            "domain_name": domain.name,
            "group_name": group.name,
        }
        mapping = self.keystone_client.federation.mappings.create(
            mapping_id="openid_mapping",
            rules=json.loads(rules),
        )
        prov = self.keystone_client.federation.identity_providers.create(
            id=_FEDERATED_IDENTITY_PROVIDER_NAME,
            remote_ids=[issuer_url,],
            domain_id=domain.id,
            enabled=True
        )
        protocol = self.keystone_client.federation.protocols.create(
            protocol_id=_OPENID_PROTOCOL_NAME,
            identity_provider=prov,
            mapping=mapping,
        )

    def ensure_integrations(self):
        status = zaza.sync_wrapper(
            self.openstack_model.get_status
        )()
        iam_saas = status.remote_applications.get(
            self.iam_saas_name, None
        )
        iam_certs = status.remote_applications.get(
            self.iam_saas_cert_name, None
        )

        oauth_relations = iam_saas.relations.get("oauth", [])
        cert_relations = iam_certs.relations.get("send-ca-cert", [])

        if self.application_name not in cert_relations:
            zaza.sync_wrapper(self.openstack_model.integrate)(
                f"{self.iam_saas_cert_name}:send-ca-cert",
                f"{self.application_name}:receive-ca-cert"
            )
            self._wait_for_settle()

        if self.application_name not in oauth_relations:
            zaza.sync_wrapper(self.openstack_model.integrate)(
                f"{self.iam_saas_name}:oauth",
                f"{self.application_name}:oauth"
            )
            self._wait_for_settle()

    def ensure_offers_consumed(self):
        status = zaza.sync_wrapper(
            self.openstack_model.get_status
        )()
        iam_saas = status.remote_applications.get(
            self.iam_saas_name, None
        )
        iam_certs = status.remote_applications.get(
            self.iam_saas_cert_name, None
        )
        if not iam_certs:
            zaza.sync_wrapper(self.openstack_model.consume)(
                self.oauth_cert_offer_url,
                application_alias=self.iam_saas_cert_name,
            )
        if not iam_saas:
            zaza.sync_wrapper(self.openstack_model.consume)(
                self.oauth_offer_url, application_alias=self.iam_saas_name
            )


def create_oauth_and_cert_offers():
    setup = SetupOffersAndRelations()
    setup.ensure_offers()
    setup.ensure_offers_consumed()
    setup.ensure_integrations()
    setup.create_federated_domain()


class IdentityTests(test_utils.BaseCharmTest):
    application_name = "keystone"
    ca_file = None

    @classmethod
    def setUpClass(cls):
        super(IdentityTests, cls).setUpClass(
            application_name=cls.application_name)
        cls.iam_model_name = cls.model_aliases.get("iam", None)
        if not cls.iam_model_name:
            raise ValueError("could not get 'iam' model alias")
        cls.iam_model = zaza.sync_wrapper(model.get_model)(
            model_name=cls.iam_model_name)
        auth_data = openstack_utils.get_overcloud_auth()
        cls.keystone_client = openstack_utils.get_keystone_client(auth_data)
        cls.admin_account = cls._get_admin_account(cls)

    @property
    def _ca_cert(self):
        if self.ca_file:
            return self.ca_file

        rel_info = _get_unit_rel_info("keystone/0")
        ca_and_chain = []
        for rel in rel_info:
            ep = rel.get("endpoint")
            if ep == "receive-ca-cert":
                for unit, unit_data in rel.get("related-units", {}).items():
                    data = unit_data.get("data", {})
                    ca = data.get("ca")
                    if ca:
                        ca_and_chain.append(ca)
                    chain = json.loads(data.get("chain", '[]'))
                    if chain:
                        data.extend(chain)
        self.ca_file = tempfile.NamedTemporaryFile(delete=False).name
        with open(self.ca_file, "w") as fd:
            fd.write("\n".join(ca_and_chain))
        return self.ca_file

    def _create_client_creds(self):
        result = model.run_action_on_leader(
            "hydra",
            "create-oauth-client",
            model_name=self.iam_model_name,
            action_params={
                "grant-types": ["authorization_code", "client_credentials"],
                "response-types": ["id_token", "code", "token"],
                "scope": ["openid", "email", "profile"],
            },
            raise_on_failure=True,
        )
        data = result.data
        client_id = data.get("results", {}).get("client-id")
        client_secret = data.get("results", {}).get("client-secret")
        self.assertTrue(None not in [client_id, client_secret])
        return {
            "client_id": client_id,
            "client_secret": client_secret
        }

    def _get_admin_account(self):
        result = model.run_action_on_leader(
            "keystone",
            "get-admin-account",
            model_name=self.model_name,
            raise_on_failure=True,
        )
        results = result.data.get("results", {})
        return results

    def test_oauth_client_creds(self):
        issuer_url = _get_issuer_url()
        creds = self._create_client_creds()
        discovery_ep = "%s/.well-known/openid-configuration" % issuer_url
        auth = v3.OidcClientCredentials(
            scope="openid email profile",
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            identity_provider=_FEDERATED_IDENTITY_PROVIDER_NAME,
            protocol=_OPENID_PROTOCOL_NAME,
            discovery_endpoint=discovery_ep,
            auth_url=self.admin_account["public-endpoint"],
            project_domain_name=_FEDERATED_DOMAIN_NAME,
            project_name=_FEDERATED_PROJECT_NAME
        )
        ks_session = session.Session(auth=auth, verify=self._ca_cert)
        ks_client = keystoneclient.Client(session=ks_session)

        token = ks_session.get_token()
        token_data = ks_client.tokens.get_token_data(token)
        user_details = ks_client.users.get(user=token_data["token"]["user"]["id"])
        self.assertEqual(user_details.id, token_data["token"]["user"]["id"])

    def test_horizon_relations_created(self):
        rel_info = _get_unit_rel_info("horizon/0")
        trusted_dashboard_found = False
        for rel in rel_info:
            if rel.get("endpoint") == "trusted-dashboard":
                rel_units = rel.get("related-units")
                self.assertTrue(rel_units)
                self.assertTrue(rel_units.get("keystone/0", False))
                trusted_dashboard_found = True
                app_data = rel.get("application-data", {})
                fid_providers = app_data.get("federated-providers")
                self.assertTrue(fid_providers)
                data = json.loads(fid_providers)
                self.assertTrue(len(data) > 0)
                self.assertEqual(
                    data[0].get("name"),
                    _FEDERATED_IDENTITY_PROVIDER_NAME,
                )
                self.assertEqual(data[0].get("protocol"), _OPENID_PROTOCOL_NAME)
        self.assertTrue(trusted_dashboard_found)

    def test_keystone_relations_created(self):
        rel_info = _get_unit_rel_info(self.lead_unit)

        oauth_found = False
        iam_certs_found = False
        trusted_dashboard_found = False
        for rel in rel_info:
            if rel.get("endpoint") == "oauth" and rel.get("cross-model"):
                rel_units = rel.get("related-units")
                self.assertTrue(rel_units)
                self.assertTrue(rel_units.get("canonical-identity-platform/0", False))
                oauth_found = True
            if rel.get("endpoint") == "receive-ca-cert" and rel.get("cross-model"):
                rel_units = rel.get("related-units")
                self.assertTrue(rel_units)
                self.assertTrue(rel_units.get("iam-cert/0", False))
                iam_certs_found = True
            if rel.get("endpoint") == "trusted-dashboard":
                rel_units = rel.get("related-units")
                self.assertTrue(rel_units)
                self.assertTrue(rel_units.get("horizon/0", False))
                trusted_dashboard_found = True
        self.assertTrue(oauth_found)
        self.assertTrue(iam_certs_found)
        self.assertTrue(trusted_dashboard_found)
