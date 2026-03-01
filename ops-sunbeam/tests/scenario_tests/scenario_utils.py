#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
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

"""Utilities for writing sunbeam scenario tests."""

import functools
import itertools
import json

from ops import testing

# Data used to create Relation objects. If an incomplete relation is being
# created only the 'endpoint', 'interface' and 'remote_app_name' key are
# used.
default_relations = {
    "amqp": {
        "endpoint": "amqp",
        "interface": "rabbitmq",
        "remote_app_name": "rabbitmq",
        "remote_app_data": {
            "hostname": "rabbithost1.local",
            "password": "rabbit.pass",
        },
        "remote_units_data": {0: {"ingress-address": "10.0.0.13"}},
    },
    "identity-credentials": {
        "endpoint": "identity-credentials",
        "interface": "keystone-credentials",
        "remote_app_name": "keystone",
        "remote_app_data": {
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "credentials": "secret:foo",
            "project-name": "user-project",
            "project-id": "uproj-id",
            "user-domain-name": "udomain-name",
            "user-domain-id": "udomain-id",
            "project-domain-name": "pdomain_-ame",
            "project-domain-id": "pdomain-id",
            "region": "region12",
            "public-endpoint": "http://10.20.21.11:80/openstack-keystone",
            "internal-endpoint": "http://10.153.2.45:80/openstack-keystone",
        },
    },
    "database": {
        "endpoint": "database",
        "interface": "mysql_client",
        "remote_app_name": "mysql",
        "remote_app_data": {
            "secret-user": "secret:db-creds",
            "endpoints": "10.0.0.10",
        },
        "remote_units_data": {0: {"ingress-address": "10.0.0.3"}},
    },
    "ingress-internal": {
        "endpoint": "ingress-internal",
        "interface": "ingress",
        "remote_app_name": "traefik-internal",
        "remote_app_data": {
            "ingress": json.dumps({"url": "http://internal-url"}),
        },
        "remote_units_data": {0: {}},
    },
    "ingress-public": {
        "endpoint": "ingress-public",
        "interface": "ingress",
        "remote_app_name": "traefik-public",
        "remote_app_data": {
            "ingress": json.dumps({"url": "http://public-url"}),
        },
        "remote_units_data": {0: {}},
    },
    "certificates": {
        "endpoint": "certificates",
        "interface": "tls-certificates",
        "remote_app_name": "vault",
        "remote_app_data": {"certificates": "TEST_CERT_LIST"},
        "remote_units_data": {0: {}},
    },
    "logging": {
        "endpoint": "logging",
        "interface": "loki_push_api",
        "remote_app_name": "loki",
        "remote_app_data": {},
        "remote_units_data": {
            0: {
                "endpoint": json.dumps(
                    {"url": "http://10.20.23.1/cos-loki-0/loki/api/v1/push"}
                ),
            },
        },
    },
}

# Secrets required by specific relation types.
# Maps remote_app_name to a factory returning a testing.Secret.
_relation_secrets = {
    "mysql": lambda: testing.Secret(
        tracked_content={"username": "foo", "password": "hardpassword"},
        id="secret:db-creds",
        owner=None,
    ),
    "keystone": lambda: testing.Secret(
        tracked_content={"username": "svcuser1", "password": "svcpass1"},
        id="secret:foo",
        owner=None,
    ),
}


def relation_combinations(
    metadata, one_missing=False, incomplete_relation=False
):
    """Based on a charms metadata generate tuples of relations.

    :param metadata: Dict of charm metadata
    :param one_missing: Bool if set then each unique relations tuple will be
                             missing one relation.
    :param one_missing: Bool if set then each unique relations tuple will
                             include one relation that has missing relation
                             data
    """
    _incomplete_relations = []
    _complete_relations = []
    _relation_pairs = []
    for rel_name in metadata.get("requires", {}):
        rel = default_relations[rel_name]
        complete_relation = testing.Relation(
            endpoint=rel["endpoint"],
            remote_app_name=rel["remote_app_name"],
            remote_app_data=rel.get("remote_app_data", {}),
            remote_units_data=rel.get("remote_units_data", {}),
        )
        relation_missing_data = testing.Relation(
            endpoint=rel["endpoint"],
            remote_app_name=rel["remote_app_name"],
        )
        _incomplete_relations.append(relation_missing_data)
        _complete_relations.append(complete_relation)
        _relation_pairs.append([relation_missing_data, complete_relation])

    if not (one_missing or incomplete_relation):
        return [tuple(_complete_relations)]
    if incomplete_relation:
        relations = list(itertools.product(*_relation_pairs))
        relations.remove(tuple(_complete_relations))
        return relations
    if one_missing:
        event_count = range(len(_incomplete_relations))
    else:
        event_count = range(len(_incomplete_relations) + 1)
    combinations = []
    for i in event_count:
        combinations.extend(
            list(itertools.combinations(_incomplete_relations, i))
        )
    return combinations


missing_relation = functools.partial(
    relation_combinations, one_missing=True, incomplete_relation=False
)
incomplete_relation = functools.partial(
    relation_combinations, one_missing=False, incomplete_relation=True
)
complete_relation = functools.partial(
    relation_combinations, one_missing=False, incomplete_relation=False
)


def get_secrets_for_relations(relations):
    """Create secrets required by the given relations.

    Returns a list of testing.Secret objects needed by the relations
    (e.g. identity-credentials and database both use secrets).
    """
    secrets = []
    seen = set()
    for relation in relations:
        factory = _relation_secrets.get(relation.remote_app_name)
        if factory and relation.remote_app_name not in seen:
            seen.add(relation.remote_app_name)
            secrets.append(factory())
    return secrets


def get_keystone_secret_definition(relations):
    """Create the keystone identity secret.

    Kept for backward compatibility; prefer get_secrets_for_relations().
    """
    for relation in relations:
        if relation.remote_app_name == "keystone":
            return testing.Secret(
                tracked_content={
                    "username": "svcuser1",
                    "password": "svcpass1",
                },
                id="secret:foo",
                owner=None,
            )
    return None
