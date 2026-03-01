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

"""Scenario (ops.testing state-transition) tests for keystone-ldap-k8s."""

import base64
import json

from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
    peer_relation,
)

LDAP_CONFIG_FLAGS = json.dumps(
    {
        "group_tree_dn": "ou=groups,dc=test,dc=com",
        "group_objectclass": "posixGroup",
        "group_name_attribute": "cn",
        "group_member_attribute": "memberUid",
        "group_members_are_ids": "true",
        "url": "ldap://10.1.176.184",
        "user": "cn=admin,dc=test,dc=com",
        "password": "crapper",
        "suffix": "dc=test,dc=com",
    }
)


class TestConfigChanged:
    """Config-changed with peer relation as leader → active."""

    def test_active_as_leader(self, ctx, base_state):
        """Leader with peer relation reaches active."""
        state_out = ctx.run(ctx.on.config_changed(), base_state)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_waiting_non_leader(self, ctx):
        """Non-leader waits for leader readiness."""
        state_in = testing.State(
            leader=False,
            relations=[peer_relation()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "Leader not ready")


class TestDomainConfigRelation:
    """Domain-config relation data is populated correctly."""

    def test_domain_config_set_on_relation(
        self, ctx, base_state, domain_config_relation
    ):
        """Config-changed with domain-config relation and config sets relation data."""
        state_in = testing.State(
            leader=True,
            relations=list(base_state.relations) + [domain_config_relation],
            config={
                "domain-name": "userdomain",
                "ldap-config-flags": LDAP_CONFIG_FLAGS,
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")

        dc_rel = state_out.get_relation(domain_config_relation.id)
        app_data = dc_rel.local_app_data
        assert app_data["domain-name"] == "userdomain"

        contents = base64.b64decode(app_data["config-contents"]).decode()
        assert "password = crapper" in contents
        assert "group_objectclass = posixGroup" in contents
        assert "[ldap]" in contents
        assert "[identity]" in contents
        assert "driver = ldap" in contents

    def test_domain_config_no_domain_name(
        self, ctx, base_state, domain_config_relation
    ):
        """Config-changed without domain-name errors when domain-config relation exists."""
        state_in = testing.State(
            leader=True,
            relations=list(base_state.relations) + [domain_config_relation],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        # domain-name config is None, charm tries to set it on relation → blocked
        assert_unit_status(state_out, "blocked")

    def test_domain_config_with_tls_ca(
        self, ctx, base_state, domain_config_relation
    ):
        """TLS CA certificate is set on the relation when configured."""
        ca_cert = (
            "-----BEGIN CERTIFICATE-----\nMIItest\n-----END CERTIFICATE-----"
        )
        state_in = testing.State(
            leader=True,
            relations=list(base_state.relations) + [domain_config_relation],
            config={
                "domain-name": "userdomain",
                "ldap-config-flags": LDAP_CONFIG_FLAGS,
                "tls-ca-ldap": ca_cert,
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        dc_rel = state_out.get_relation(domain_config_relation.id)
        app_data = dc_rel.local_app_data
        decoded_ca = base64.b64decode(app_data["ca"]).decode()
        assert decoded_ca == ca_cert

    def test_domain_config_invalid_json_flags(
        self, ctx, base_state, domain_config_relation
    ):
        """Invalid JSON in ldap-config-flags still reaches active (logged error)."""
        state_in = testing.State(
            leader=True,
            relations=list(base_state.relations) + [domain_config_relation],
            config={
                "domain-name": "userdomain",
                "ldap-config-flags": "not-valid-json{",
            },
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)

        assert state_out.unit_status == testing.ActiveStatus("")

        dc_rel = state_out.get_relation(domain_config_relation.id)
        app_data = dc_rel.local_app_data
        assert app_data["domain-name"] == "userdomain"
        # Template renders with empty config dict
        contents = base64.b64decode(app_data["config-contents"]).decode()
        assert "[ldap]" in contents
        assert "[identity]" in contents
