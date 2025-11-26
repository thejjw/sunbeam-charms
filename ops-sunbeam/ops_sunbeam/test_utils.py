#!/usr/bin/env python3

# Copyright 2020 Canonical Ltd.
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

"""Module containing shared code to be used in a charms units tests."""

import collections
import inspect
import ipaddress
import json
import os
import pathlib
import sys
import typing
import unittest
from datetime import (
    datetime,
    timedelta,
)
from typing import (
    BinaryIO,
    Dict,
    List,
    Optional,
    TextIO,
    Union,
)
from unittest.mock import (
    MagicMock,
    Mock,
    PropertyMock,
    patch,
)

import ops
import ops.storage
import yaml
from ops_sunbeam.charm import (
    OSBaseOperatorCharm,
)

sys.path.append("lib")  # noqa
sys.path.append("src")  # noqa

from ops import (
    framework,
    model,
)
from ops._private.harness import (
    _TestingModelBackend,
    _TestingPebbleClient,
)
from ops.jujucontext import (
    JujuContext,
)
from ops.testing import (
    Harness,
)

TEST_CA = """-----BEGIN CERTIFICATE-----
MIIDADCCAeigAwIBAgIUOTGfdiGSlKoiyWskxH1za0Nh7cYwDQYJKoZIhvcNAQEL
BQAwGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MB4XDTIyMDIwNjE4MjYyM1oX
DTMzMDEyMDE4MjYyM1owRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENl
cnRpZmljYXRlIEF1dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMvzFo76z05TU8ECnXpJC2b1mMQK6r5FD+9K
CwxPUr6l5ar0rm3+CM/MQA0RBrR17Ql8kZab7gSEcVbbUUM825zqoin+ECsaYttb
kYMHt5lhgEEPwOn9kWC2wh8bBym1eR1zZnpcy0UrclaZByQ7BH+KG3ENi0vozuxp
xVgQV06wjBC9Bl3WeaUtMiYb/7CqPgTgZPBDL97eae8H3A29U5Xpr/qGf2Gx27pN
zAyxOsuSDwSB8NrVEZRYAT/kvLku0c/ZmZpU2xIVOOsUkTF+r6b2OfLnqRajl7zs
KatfnQUb4tCFZ3IO83VvlHS54PxDflTOb5qGSe1r21RTfM9gjmsCAwEAAaMTMBEw
DwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEAUVXG2lGye4RV4NWZ
rZ6OWmgzy3/wlMKRAt8tXsB2uaFqxg7QzIMfFsLCgRF5xJNS1faHmJIK391or3ip
ZNgygS4eqWgBqqds60bB4s0JW+QEVfyKeB/tZHm83fZgEypwOs9N0EW/xLslNaFe
zT8PgdjdzBW80l7KAMy4/GzZvvK7MWfkkhwwnY7oXs9F3q28gFIdcYyc9A1SDg/8
8jWI6RP5yBcNS/PgUmVV+Ko1uTHxNsKjOn7QPuUgjMBeW0fpBCHVFxz7rs+orHNF
JSWcYpOxivTh+YO8cAxAGlKzrgZDcXQDjGfF34U/v3niDUHO+CAk6Jz3io4Oxh2X
GksTPQ==
-----END CERTIFICATE-----"""

TEST_CHAIN = """-----BEGIN CERTIFICATE-----
MIIDADCCAeigAwIBAgIUOTGfdiGSlKoiyWskxH1za0Nh7cYwDQYJKoZIhvcNAQEL
BQAwGjEYMBYGA1UEAwwPRGl2aW5lQXV0aG9yaXR5MB4XDTIyMDIwNjE4MjYyM1oX
DTMzMDEyMDE4MjYyM1owRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENl
cnRpZmljYXRlIEF1dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTCCASIwDQYJKoZI
hvcNAQEBBQADggEPADCCAQoCggEBAMvzFo76z05TU8ECnXpJC2b1mMQK6r5FD+9K
CwxPUr6l5ar0rm3+CM/MQA0RBrR17Ql8kZab7gSEcVbbUUM825zqoin+ECsaYttb
kYMHt5lhgEEPwOn9kWC2wh8bBym1eR1zZnpcy0UrclaZByQ7BH+KG3ENi0vozuxp
xVgQV06wjBC9Bl3WeaUtMiYb/7CqPgTgZPBDL97eae8H3A29U5Xpr/qGf2Gx27pN
zAyxOsuSDwSB8NrVEZRYAT/kvLku0c/ZmZpU2xIVOOsUkTF+r6b2OfLnqRajl7zs
KatfnQUb4tCFZ3IO83VvlHS54PxDflTOb5qGSe1r21RTfM9gjmsCAwEAAaMTMBEw
DwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEAUVXG2lGye4RV4NWZ
rZ6OWmgzy3/wlMKRAt8tXsB2uaFqxg7QzIMfFsLCgRF5xJNS1faHmJIK391or3ip
ZNgygS4eqWgBqqds60bB4s0JW+QEVfyKeB/tZHm83fZgEypwOs9N0EW/xLslNaFe
zT8PgdjdzBW80l7KAMy4/GzZvvK7MWfkkhwwnY7oXs9F3q28gFIdcYyc9A1SDg/8
8jWI6RP5yBcNS/PgUmVV+Ko1uTHxNsKjOn7QPuUgjMBeW0fpBCHVFxz7rs+orHNF
JSWcYpOxivTh+YO8cAxAGlKzrgZDcXQDjGfF34U/v3niDUHO+CAk6Jz3io4Oxh2X
GksTPQ==
-----END CERTIFICATE-----"""

TEST_SERVER_CERT = """-----BEGIN CERTIFICATE-----
MIIEEzCCAvugAwIBAgIUIRVQ0iFgTDBP+Ju6AlcnxTHywUgwDQYJKoZIhvcNAQEL
BQAwRTFDMEEGA1UEAxM6VmF1bHQgSW50ZXJtZWRpYXRlIENlcnRpZmljYXRlIEF1
dGhvcml0eSAoY2hhcm0tcGtpLWxvY2FsKTAeFw0yMjAyMDcxODI1NTlaFw0yMzAy
MDcxNzI2MjhaMCsxKTAnBgNVBAMTIGp1anUtOTNiMDlkLXphemEtYWMzMDBhNjEz
OTI2LTExMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4VYeKjC3o9GZ
AnbuVBudyd/a5sHnaGZlMJz8zevhGr5nARRR194bgR8VSB9k1fRbF1Y9WTygBW5a
iXPy+KbmaD5DsDpJNkF/2zOQDLG9nKmLbamrAcHFU8l8kAVwkdhYgu3T8QbLksoz
YPiYavg9KfA51wVxTRuUyLpvSLJkc1q0xwuJiE6d46Grdpfyve9cS4G9JxLUL1S9
HPMIT6rO25AKepPbtGMU/MN/yj/qfqWKga/X/bQzPyQB2UjNFI/0kn3iBi+yJRmI
3o7ku0exd75eRhMPR7FyG9yfgMroK3FjSJE5fj73akkEd4SW8FgyaeUeoeYxj1G+
sVaLm6aBbwIDAQABo4IBEzCCAQ8wDgYDVR0PAQH/BAQDAgOoMB0GA1UdJQQWMBQG
CCsGAQUFBwMBBggrBgEFBQcDAjAdBgNVHQ4EFgQUBwPuvsOqVMzZke3aVEQTzcXC
EDwwSgYIKwYBBQUHAQEEPjA8MDoGCCsGAQUFBzAChi5odHRwOi8vMTcyLjIwLjAu
MjI3OjgyMDAvdjEvY2hhcm0tcGtpLWxvY2FsL2NhMDEGA1UdEQQqMCiCIGp1anUt
OTNiMDlkLXphemEtYWMzMDBhNjEzOTI2LTExhwSsFABAMEAGA1UdHwQ5MDcwNaAz
oDGGL2h0dHA6Ly8xNzIuMjAuMC4yMjc6ODIwMC92MS9jaGFybS1wa2ktbG9jYWwv
Y3JsMA0GCSqGSIb3DQEBCwUAA4IBAQBr3WbXVesJ4R2P1Z67BS+wy9a1JYRLtn7l
yS+XoEYKhpbxTZh0q74sAhGxoSlvc9GGyeeIsXzndw6pbGyK6WCOmJoelWIYr0Be
wzSbqkarasPFVpPJnFAGqry6y5B3lZ3OrhHJOIwMSOMQfPt2dSsz+HqfrMwxqAek
smciCVWqVwN+uq0yqeH5QuACHlkJSV4o/5SkDcFZFaFHuTRqd6hMpczZIw+o+NRn
OO1YV69oqCCfUE01zlwTF7thZA19xacGS9f8GJO9Ij15MiysZLjxoTfoof/wDdNd
A0Rs/pW3ja1UfTItPdjC4BgWtQh1a7O9NznrW2L6nRCASI0F1FvQ
-----END CERTIFICATE-----"""

TEST_SERVER_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEA4VYeKjC3o9GZAnbuVBudyd/a5sHnaGZlMJz8zevhGr5nARRR
194bgR8VSB9k1fRbF1Y9WTygBW5aiXPy+KbmaD5DsDpJNkF/2zOQDLG9nKmLbamr
AcHFU8l8kAVwkdhYgu3T8QbLksozYPiYavg9KfA51wVxTRuUyLpvSLJkc1q0xwuJ
iE6d46Grdpfyve9cS4G9JxLUL1S9HPMIT6rO25AKepPbtGMU/MN/yj/qfqWKga/X
/bQzPyQB2UjNFI/0kn3iBi+yJRmI3o7ku0exd75eRhMPR7FyG9yfgMroK3FjSJE5
fj73akkEd4SW8FgyaeUeoeYxj1G+sVaLm6aBbwIDAQABAoIBAQC8O84y/ENLa5lf
v63TQMaMjp0zyqLeSTsaYumjsvl197vf4POFWhqrwCVs/BylxdwaIIZa9xPNtaOX
0u4S3Ij4Z5rvqaDi29BMckRQ9mEob1DzqJobe5y1I0kUnhatHobByJ4VZ9HCq3pD
9SaNpRSi5fPLNNayzOl6zJKNrcfPu1IA085oCzANmFBPM9+3H4xOgIT9f/0ypw24
F9iZ6SEp6p81iTvlPB7FSLakMAww3V63M9E92drA2sB2veDRfR8/vHoEdL5vhYZU
v4/GdwzByL2IplJLB1I9fsITzZs9DXdw6+musOq9i8u8R1G6IickPslUegn+PPFR
vcDP69dxAoGBAP45mzH/qYKhbe9Vf+OJgU0is0gEeixlTeiIFhEU6AjGr7/2rTX5
7Etzdc0muCc5Atepf82pqoY3Ns8kE/FGbmFJTGTsFIK+GAdMDaH0IDG1zUoBbOqL
58Xrq42wEX2CuCeCHTiHSVsB4/uY+IfzOa+t+CrwczZl3+i/4PrKCaZ9AoGBAOLo
4IHmenDgBSbQIWOAaUrO2jTWjsRNIDOO0tfkJCnT/bLgaWK1Lg313gD87PF+/sFM
6TakFC9e0ieLKDKbT6aML1uF3nTl3qkE2K771PM57w/w3zdPalRbbpTgJc4BWhJc
iqSPsrUYfHvy5IpbdMnzKRbOGR9Hc6bx3aA+Aw9bAoGAZsHuIyWN5MlPYGAU02nv
I7iU8tUsdOl1tjnbgYgLyhBVVahllt2wT0caJJQz91ap+XX/vKeJz7pdoxiYHvwy
/YvdHyX1nGst1zU8hWvh33X2xqUQ2zU1t+BsdVbnmu3Nddq36PN2CR0Yg8fvHTSI
6qPNHb4XM7O176QvUe98OxkCgYB5AucQf+EWp3I349GaphYBLlXSzgYvjE47ENVD
C8l5gTQQnHu3h5Z7HX97GWgn1ql4X1MUr+aP6Mq9CgqzCn8s/CAZeEhOIXVgwFPq
5iUIXgIvhy8T6Ud0m5pazTt8JN5rYm0SHAybZeall8DoRKQBO6vTHLDrLIjyJJUk
a03odwKBgG454yINXnHPBo9jjcEKwBTaMLH0n25HMJmWaJUnGVmPzrhxHp5xMKZz
ULTaKTN2gp7E2BuxENtAyplrvLiXXYH3CqT528JgMdMm0al6X3MXo9WqbOg/KNpa
4JSyyuZ42yGmYlhMCimlk3kVnDxb8PJLWOFnx6f9/i0RWUqnY0nU
-----END RSA PRIVATE KEY-----"""

TEST_CSR = """-----BEGIN CERTIFICATE REQUEST-----
MIICxTCCAa0CAQAwRzEWMBQGA1UEAwwNb3ZuLWNlbnRyYWwtMDEtMCsGA1UELQwk
ZTFhZjIxMmEtNTUxOC00MjkyLWIxZTktZGM3NThlZTZkMzEwMIIBIjANBgkqhkiG
9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzrJfdNfHKyzu9dAnoEi2DE9nTQnkUjGVIMXg
8oCy5NgIm2ORoeXrdrr2ODIUepxX7M40cWmvoFiABK6DGgpP3wh6XosWftIc8hrX
/KaHtL4iru+dKJF9d5TNe+vWoFY1jh3k/+c+F59UhdoRbw4QcSTgLBvsb/XC8pdD
gc96GWgzyA5exZN9xXg8dvHCMKLLzCkAgHkMlkSPB6Ghi9bUeeXIYRvU8D+EMh+R
hKaSsvOxRyISgkvE0cGQ86NIuXkNvvvr8bFYNLMxBNkjZrqlZyqhEsq0eZAAm5iu
Fi33z3uBaA5d7V7wbYAxWuFlckdGTHql3vO/W2X3PHbT/TEBxQIDAQABoDkwNwYJ
KoZIhvcNAQkOMSowKDAmBgNVHREEHzAdggwxMC4xLjEwNy4yMTSCDTEwLjE1Mi4x
ODMuMTkwDQYJKoZIhvcNAQELBQADggEBADJSto8T6XiMdjMKekhS6SsQKNyijVJ0
cJr7x1u8FLEbCWlLRO9kMroz4i4iSu5xYcewNsRioiN4A56FuoOE8qCjzAHczR/8
Anah4rYJFt7wCu+RxfHEvBmSYgV0Rbq/KwjYnclCpTu/m5yrUmsI092+2AaOB/nA
c0Npr5oZZPeWL4S3+c02IxCeH1EwxIQtfprA/VgCWpEU25ImQb/c14KF5EQEHhv6
A5qVqdCCg4LlNrqiFYyVtHqDco+voq4W95KkkUYe20o16qOTwpR72qn75DagO/8I
R3iMBPwkhi4+igbliU/EMLltTj8pMilUhc1Ewuji4QZhsM2qxgZkcBk=
-----END CERTIFICATE REQUEST-----"""


class ContainerCalls:
    """Object to log container calls."""

    def __init__(self) -> None:
        """Init container calls."""
        self.start: dict[str, list[list[str]]] = collections.defaultdict(list)
        self.stop: dict[str, list[list[str]]] = collections.defaultdict(list)
        self.push: dict[str, list[dict]] = collections.defaultdict(list)
        self.pull: dict[str, list[str]] = collections.defaultdict(list)
        self.execute: dict[str, list[list[str]]] = collections.defaultdict(
            list
        )
        self.remove_path: dict[str, list[str]] = collections.defaultdict(list)

    def add_start(self, container_name: str, call: list[str]) -> None:
        """Log a start call."""
        self.start[container_name].append(call)

    def add_stop(self, container_name: str, call: list[str]) -> None:
        """Log a stop call."""
        self.stop[container_name].append(call)

    def started_services(self, container_name: str) -> List:
        """Distinct unordered list of services that were started."""
        return list(
            set(
                [
                    svc
                    for svc_list in self.start[container_name]
                    for svc in svc_list
                ]
            )
        )

    def stopped_services(self, container_name: str) -> List:
        """Distinct unordered list of services that were started."""
        return list(
            set(
                [
                    svc
                    for svc_list in self.stop[container_name]
                    for svc in svc_list
                ]
            )
        )

    def add_push(self, container_name: str, call: dict) -> None:
        """Log a push call."""
        self.push[container_name].append(call)

    def add_pull(self, container_name: str, call: str) -> None:
        """Log a pull call."""
        self.pull[container_name].append(call)

    def add_execute(self, container_name: str, call: list[str]) -> None:
        """Log a execute call."""
        self.execute[container_name].append(call)

    def add_remove_path(self, container_name: str, call: str) -> None:
        """Log a remove path call."""
        self.remove_path[container_name].append(call)

    def updated_files(self, container_name: str) -> list:
        """Return a list of files that have been updated in a container."""
        return [c["path"] for c in self.push.get(container_name, [])]

    def file_update_calls(self, container_name: str, file_name: str) -> list:
        """Return the update call for File_name in container_name."""
        return [
            c
            for c in self.push.get(container_name, [])
            if c["path"] == file_name
        ]


class CharmTestCase(unittest.TestCase):
    """Class to make mocking easier."""

    container_calls = ContainerCalls()

    def setUp(self, obj: typing.Any, patches: list) -> None:  # type: ignore
        """Run constructor."""
        super().setUp()
        self.patches = patches
        self.obj = obj
        self.patch_all()

    def patch(self, method: typing.Any) -> Mock:
        """Patch the named method on self.obj."""
        _m = patch.object(self.obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def patch_obj(self, obj: "typing.Any", method: "typing.Any") -> Mock:
        """Patch the named method on obj."""
        _m = patch.object(obj, method)
        mock = _m.start()
        self.addCleanup(_m.stop)
        return mock

    def patch_all(self) -> None:
        """Patch all objects in self.patches."""
        for method in self.patches:
            setattr(self, method, self.patch(method))

    def check_file(
        self,
        container: str,
        path: str,
        contents: list | None = None,
        user: str | None = None,
        group: str | None = None,
        permissions: str | None = None,
    ) -> None:
        """Check the attributes of a file."""
        client = self.harness.charm.unit.get_container(container)._pebble  # type: ignore
        files = client.list_files(path, itself=True)
        self.assertEqual(len(files), 1)
        test_file = files[0]
        self.assertEqual(test_file.path, path)
        if contents:
            with client.pull(path) as infile:
                received_data = infile.read()
            self.assertEqual(contents, received_data)
        if user:
            self.assertEqual(test_file.user_id, user)
        if group:
            self.assertEqual(test_file.group_id, group)
        if permissions:
            self.assertEqual(test_file.permissions, permissions)


def add_ingress_relation(harness: Harness, endpoint_type: str) -> int:
    """Add ingress relation."""
    app_name = "traefik-" + endpoint_type
    unit_name = app_name + "/0"
    rel_name = "ingress-" + endpoint_type
    rel_id = harness.add_relation(rel_name, app_name)
    harness.add_relation_unit(rel_id, unit_name)
    return rel_id


def add_ingress_relation_data(
    harness: Harness, rel_id: int, endpoint_type: str
) -> None:
    """Add ingress data to ingress relation."""
    app_name = "traefik-" + endpoint_type
    url = "http://" + endpoint_type + "-url"

    ingress_data = {"url": url}
    harness.update_relation_data(
        rel_id, app_name, {"ingress": json.dumps(ingress_data)}
    )


def add_complete_ingress_relation(harness: Harness) -> None:
    """Add complete Ingress relation."""
    for endpoint_type in ["internal", "public"]:
        rel_id = add_ingress_relation(harness, endpoint_type)
        add_ingress_relation_data(harness, rel_id, endpoint_type)


def add_base_amqp_relation(harness: Harness) -> int:
    """Add amqp relation."""
    rel_id = harness.add_relation("amqp", "rabbitmq")
    harness.add_relation_unit(rel_id, "rabbitmq/0")
    harness.add_relation_unit(rel_id, "rabbitmq/0")
    harness.update_relation_data(
        rel_id, "rabbitmq/0", {"ingress-address": "10.0.0.13"}
    )
    return rel_id


def add_amqp_relation_credentials(harness: Harness, rel_id: int) -> None:
    """Add amqp data to amqp relation."""
    harness.update_relation_data(
        rel_id,
        "rabbitmq",
        {"hostname": "rabbithost1.local", "password": "rabbit.pass"},
    )


def add_base_ceph_access_relation(harness: Harness) -> int:
    """Add ceph-access relation."""
    rel_id = harness.add_relation(
        "ceph-access", "cinder-ceph", app_data={"a": "b"}
    )
    return rel_id


def add_ceph_access_relation_response(harness: Harness, rel_id: int) -> None:
    """Add secret data to cinder-access relation."""
    credentials_content = {"uuid": "svcuser1", "key": "svcpass1"}
    credentials_id = harness.add_model_secret(
        "cinder-ceph", credentials_content
    )
    harness.grant_secret(credentials_id, harness.charm.app.name)
    harness.update_relation_data(
        rel_id, "cinder-ceph", {"access-credentials": credentials_id}
    )


def add_base_identity_service_relation(harness: Harness) -> int:
    """Add identity-service relation."""
    rel_id = harness.add_relation("identity-service", "keystone")
    harness.add_relation_unit(rel_id, "keystone/0")
    harness.add_relation_unit(rel_id, "keystone/0")
    harness.update_relation_data(
        rel_id, "keystone/0", {"ingress-address": "10.0.0.33"}
    )
    return rel_id


def add_identity_service_relation_response(
    harness: Harness, rel_id: int
) -> None:
    """Add id service data to identity-service relation."""
    credentials_content = {"username": "svcuser1", "password": "svcpass1"}
    credentials_id = harness.add_model_secret("keystone", credentials_content)
    harness.grant_secret(credentials_id, harness.charm.app.name)
    harness.update_relation_data(
        rel_id,
        "keystone",
        {
            "admin-domain-id": "admindomid1",
            "admin-project-id": "adminprojid1",
            "admin-user-id": "adminuserid1",
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "service-domain": "servicedom",
            "service-domain_id": "svcdomid1",
            "service-domain-name": "svc-domain",
            "service-host": "keystone.service",
            "service-port": "5000",
            "service-protocol": "http",
            "service-project": "svcproj1",
            "service-project-name": "svc-project",
            "service-project-id": "svcprojid1",
            "service-credentials": credentials_id,
            "region": "region12",
            "project-domain-name": "svc-domain",
            "user-domain-name": "svc-domain",
        },
    )


def add_base_identity_credentials_relation(harness: Harness) -> int:
    """Add identity-service relation."""
    rel_id = harness.add_relation("identity-credentials", "keystone")
    harness.add_relation_unit(rel_id, "keystone/0")
    harness.add_relation_unit(rel_id, "keystone/0")
    harness.update_relation_data(
        rel_id, "keystone/0", {"ingress-address": "10.0.0.35"}
    )
    return rel_id


def add_identity_credentials_relation_response(
    harness: Harness, rel_id: int
) -> None:
    """Add id service data to identity-service relation."""
    credentials_content = {"username": "username", "password": "user-password"}
    credentials_id = harness.add_model_secret("keystone", credentials_content)
    harness.grant_secret(credentials_id, harness.charm.app.name)
    harness.update_relation_data(
        rel_id,
        "keystone",
        {
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "credentials": credentials_id,
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
    )


def add_base_db_relation(harness: Harness) -> int:
    """Add db relation."""
    rel_id = harness.add_relation("database", "mysql")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.add_relation_unit(rel_id, "mysql/0")
    harness.update_relation_data(
        rel_id, "mysql/0", {"ingress-address": "10.0.0.3"}
    )
    return rel_id


def add_db_relation_credentials(harness: Harness, rel_id: int) -> None:
    """Add db credentials data to db relation."""
    secret_id = harness.add_model_secret(
        "mysql", {"username": "foo", "password": "hardpassword"}
    )
    harness.grant_secret(secret_id, harness.charm.app.name)
    harness.update_relation_data(
        rel_id,
        "mysql",
        {
            "secret-user": secret_id,
            "endpoints": "10.0.0.10",
        },
    )


def add_api_relations(harness: Harness) -> None:
    """Add standard relation to api charm."""
    add_db_relation_credentials(harness, add_base_db_relation(harness))
    add_amqp_relation_credentials(harness, add_base_amqp_relation(harness))
    add_identity_service_relation_response(
        harness, add_base_identity_service_relation(harness)
    )


def add_complete_db_relation(harness: Harness) -> int:
    """Add complete DB relation."""
    rel_id = add_base_db_relation(harness)
    add_db_relation_credentials(harness, rel_id)
    return rel_id


def add_complete_identity_relation(harness: Harness) -> int:
    """Add complete Identity relation."""
    rel_id = add_base_identity_service_relation(harness)
    add_identity_service_relation_response(harness, rel_id)
    return rel_id


def add_complete_identity_credentials_relation(harness: Harness) -> int:
    """Add complete identity-credentials relation."""
    rel_id = add_base_identity_credentials_relation(harness)
    add_identity_credentials_relation_response(harness, rel_id)
    return rel_id


def add_complete_amqp_relation(harness: Harness) -> int:
    """Add complete AMQP relation."""
    rel_id = add_base_amqp_relation(harness)
    add_amqp_relation_credentials(harness, rel_id)
    return rel_id


def add_ceph_relation_credentials(harness: Harness, rel_id: int) -> None:
    """Add amqp data to amqp relation."""
    # During tests the charm class is never destroyed and recreated as it
    # would be between hook executions. This means request is never marked
    # as complete as it never matches the previous request and always looks
    # like it needs resending.
    harness.charm.ceph.interface.previous_requests = (
        harness.charm.ceph.interface.get_previous_requests_from_relations()
    )
    request = json.loads(
        harness.get_relation_data(rel_id, harness.charm.unit.name)[
            "broker_req"
        ]
    )
    client_unit = harness.charm.unit.name.replace("/", "-")
    harness.update_relation_data(
        rel_id,
        "ceph-mon/0",
        {
            "auth": "cephx",
            "key": "AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==",
            "ingress-address": "192.0.2.2",
            "ceph-public-address": "192.0.2.2",
            f"broker-rsp-{client_unit}": json.dumps(
                {"exit-code": 0, "request-id": request["request-id"]}
            ),
        },
    )
    harness.add_relation_unit(rel_id, "ceph-mon/1")


def add_base_ceph_relation(harness: Harness) -> int:
    """Add identity-service relation."""
    rel_id = harness.add_relation("ceph", "ceph-mon")
    harness.add_relation_unit(rel_id, "ceph-mon/0")
    harness.update_relation_data(
        rel_id, "ceph-mon/0", {"ingress-address": "10.0.0.33"}
    )
    return rel_id


def add_complete_ceph_relation(harness: Harness) -> int:
    """Add complete ceph relation."""
    rel_id = add_base_ceph_relation(harness)
    add_ceph_relation_credentials(harness, rel_id)
    return rel_id


def add_certificates_relation_certs(harness: Harness, rel_id: int) -> None:
    """Add cert data to certificates relation."""
    try:
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
    except ImportError:
        # Fallback if cryptography is not available
        pass

    # Get the actual CSR from the relation data that was generated by the interface
    requirer_data = harness.get_relation_data(rel_id, harness.charm.unit.name)
    actual_csr = TEST_CSR  # Default fallback

    if "certificate_signing_requests" in requirer_data:
        csr_list = json.loads(requirer_data["certificate_signing_requests"])
        if csr_list and "certificate_signing_request" in csr_list[0]:
            actual_csr = csr_list[0]["certificate_signing_request"]

            try:
                # Load the CSR to extract the public key and subject
                csr = x509.load_pem_x509_csr(actual_csr.encode())

                # Generate a matching certificate using the CSR's public key
                # Create a dummy CA private key for signing
                ca_private_key = rsa.generate_private_key(
                    public_exponent=65537,
                    key_size=2048,
                )

                # Generate the certificate with the CSR's public key
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(csr.subject)
                    .issuer_name(
                        x509.Name(
                            [
                                x509.NameAttribute(
                                    x509.NameOID.COMMON_NAME, "Test CA"
                                ),
                            ]
                        )
                    )
                    .public_key(
                        csr.public_key()  # Use the public key from the CSR
                    )
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.utcnow())
                    .not_valid_after(datetime.utcnow() + timedelta(days=365))
                    .add_extension(
                        x509.SubjectAlternativeName(
                            [
                                x509.DNSName("test.local"),
                                x509.DNSName("test"),
                                x509.IPAddress(
                                    ipaddress.ip_address("10.0.0.10")
                                ),
                            ]
                        ),
                        critical=False,
                    )
                    .sign(ca_private_key, hashes.SHA256())
                )

                generated_cert = cert.public_bytes(
                    serialization.Encoding.PEM
                ).decode()

                cert_dict = {
                    "certificate": generated_cert,
                    "certificate_signing_request": actual_csr,
                    "ca": TEST_CA,
                    "chain": [TEST_CHAIN],
                }
            except Exception:
                # Fallback to hardcoded cert
                cert_dict = {
                    "certificate": TEST_SERVER_CERT,
                    "certificate_signing_request": actual_csr,
                    "ca": TEST_CA,
                    "chain": [TEST_CHAIN],
                }
        else:
            cert_dict = {
                "certificate": TEST_SERVER_CERT,
                "certificate_signing_request": actual_csr,
                "ca": TEST_CA,
                "chain": [TEST_CHAIN],
            }
    else:
        cert_dict = {
            "certificate": TEST_SERVER_CERT,
            "certificate_signing_request": actual_csr,
            "ca": TEST_CA,
            "chain": [TEST_CHAIN],
        }

    harness.update_relation_data(
        rel_id, "vault", {"certificates": json.dumps([cert_dict])}
    )


def add_base_certificates_relation(harness: Harness) -> int:
    """Add certificates relation."""
    rel_id = harness.add_relation("certificates", "vault")
    harness.add_relation_unit(rel_id, "vault/0")

    harness.update_relation_data(
        rel_id,
        harness.charm.unit.name,
        {
            "ingress-address": "10.0.0.34",
        },
    )
    return rel_id


def add_complete_certificates_relation(harness: Harness) -> int:
    """Add complete certificates relation."""
    rel_id = add_base_certificates_relation(harness)
    add_certificates_relation_certs(harness, rel_id)
    return rel_id


def mock_get_assigned_certificate(harness: Harness) -> None:
    """Mock the get_assigned_certificate method to return fake certificates.

    This patches the TLS certificates interface's get_assigned_certificate method
    to return fake certificate data without requiring valid CSR/certificate matching.
    """
    # Create a mock ProviderCertificate object
    mock_provider_cert = MagicMock()
    mock_provider_cert.certificate = TEST_SERVER_CERT
    mock_provider_cert.certificate_signing_request = TEST_CSR
    mock_provider_cert.ca = TEST_CA
    mock_provider_cert.chain = TEST_CHAIN

    # Create a mock for the private key
    mock_private_key = TEST_SERVER_KEY

    # Define a side effect function that returns the mock certificate
    def fake_get_assigned_certificate(certificate_request):
        return (mock_provider_cert, mock_private_key)

    # Patch the interface's get_assigned_certificate method
    if hasattr(harness.charm, "certs") and hasattr(
        harness.charm.certs, "interface"
    ):
        harness.charm.certs.interface.get_assigned_certificate = (
            fake_get_assigned_certificate
        )
        # Mock the private_key property using PropertyMock
        type(harness.charm.certs.interface).private_key = PropertyMock(
            return_value=mock_private_key
        )


def add_complete_peer_relation(harness: Harness) -> int:
    """Add complete peer relation."""
    rel_id = harness.add_relation("peers", harness.charm.app.name)
    new_unit = f"{harness.charm.app.name}/1"
    harness.add_relation_unit(rel_id, new_unit)
    harness.update_relation_data(
        rel_id, new_unit, {"ingress-address": "10.0.0.35"}
    )
    return rel_id


def add_base_logging_relation(harness: Harness) -> int:
    """Add logging relation."""
    rel_id = harness.add_relation("logging", "loki")
    harness.add_relation_unit(rel_id, "loki/0")
    harness.update_relation_data(
        rel_id,
        "loki/0",
        {
            "endpoint": '{"url": "http://10.20.23.1/cos-loki-0/loki/api/v1/push"}'
        },
    )
    return rel_id


def add_complete_logging_relation(harness: Harness) -> int:
    """Add complete ceph relation."""
    rel_id = add_base_logging_relation(harness)
    return rel_id


test_relations = {
    "database": add_complete_db_relation,
    "amqp": add_complete_amqp_relation,
    "identity-service": add_complete_identity_relation,
    "identity-credentials": add_complete_identity_credentials_relation,
    "peers": add_complete_peer_relation,
    "certificates": add_complete_certificates_relation,
    "ceph": add_complete_ceph_relation,
    "logging": add_complete_logging_relation,
}


def add_all_relations(harness: Harness) -> dict[str, int]:
    """Add all the relations there are test relations for."""
    rel_ids = {}
    for key in harness._meta.relations.keys():
        if add_complete_relation := test_relations.get(key):
            rel_id = add_complete_relation(harness)
            rel_ids[key] = rel_id
    return rel_ids


def set_all_pebbles_ready(harness: Harness) -> None:
    """Set all known pebble handlers to ready."""
    for container in harness._meta.containers:
        harness.container_pebble_ready(container)


def set_remote_leader_ready(
    harness: Harness,
    rel_id: int,
) -> None:
    """Update relation data to show leader is ready."""
    harness.update_relation_data(
        rel_id, harness.charm.app.name, {"leader_ready": "true"}
    )


def get_harness(  # noqa: C901
    charm_class: type[OSBaseOperatorCharm],
    charm_metadata: str | None = None,
    container_calls: ContainerCalls | None = None,
    charm_config: str | None = None,
    charm_actions: str | None = None,
    initial_charm_config: dict | None = None,
) -> Harness:
    """Return a testing harness."""
    if container_calls is None:
        container_calls = ContainerCalls()

    class _OSTestingPebbleClient(_TestingPebbleClient):
        container_name: str

        def exec(
            self,
            command: list[str],
            *,
            service_context: Optional[str] = None,
            environment: Optional[Dict[str, str]] = None,
            working_dir: Optional[str] = None,
            timeout: Optional[float] = None,
            user_id: Optional[int] = None,
            user: Optional[str] = None,
            group_id: Optional[int] = None,
            group: Optional[str] = None,
            stdin: Optional[Union[str, bytes, TextIO, BinaryIO]] = None,
            stdout: Optional[Union[TextIO, BinaryIO]] = None,
            stderr: Optional[Union[TextIO, BinaryIO]] = None,
            encoding: Optional[str] = "utf-8",
            combine_stderr: bool = False,
        ) -> ops.pebble.ExecProcess[typing.Any]:
            container_calls.add_execute(self.container_name, command)  # type: ignore
            process_mock = MagicMock()
            process_mock.wait_output.return_value = ("", None)
            return process_mock

        def start_services(
            self,
            services: list[str],
            timeout: float = 30.0,
            delay: float = 0.1,
        ) -> ops.pebble.ChangeID:
            """Record start service events."""
            change_id = super().start_services(services, timeout, delay)
            container_calls.add_start(self.container_name, services)  # type: ignore
            return change_id

        def stop_services(
            self,
            services: List[str],
            timeout: float = 30.0,
            delay: float = 0.1,
        ) -> ops.pebble.ChangeID:
            """Record stop service events."""
            change_id = super().stop_services(services, timeout, delay)
            container_calls.add_stop(self.container_name, services)  # type: ignore
            return change_id

    class _OSTestingModelBackend(_TestingModelBackend):
        def get_pebble(self, socket_path: str) -> _OSTestingPebbleClient:
            """Get the testing pebble client."""
            container = socket_path.split("/")[
                3
            ]  # /charm/containers/<container_name>/pebble.socket
            client = self._pebble_clients.get(container, None)
            if client is None:
                container_root = self._harness_container_path / container
                container_root.mkdir()
                # Below two lines are changes from parent class method
                client = _OSTestingPebbleClient(
                    self, container_root=container_root
                )
                # Add container name to the pebble client
                client.container_name = container

                # we need to know which container a new pebble client belongs to
                # so we can figure out which storage mounts must be simulated on
                # this pebble client's mock file systems when storage is
                # attached/detached later.
                self._pebble_clients[container] = client

            self._pebble_clients_can_connect[client] = False
            return client  # type: ignore

        def network_get(  # type: ignore
            self, endpoint_name: str, relation_id: int | None = None
        ) -> dict:
            """Return a fake set of network data."""
            network_data = {
                "bind-addresses": [
                    {
                        "interface-name": "eth0",
                        "addresses": [
                            {"cidr": "10.0.0.0/24", "value": "10.0.0.10"}
                        ],
                    }
                ],
                "ingress-addresses": ["10.0.0.10"],
                "egress-subnets": ["10.0.0.0/24"],
            }
            return network_data

    filename = inspect.getfile(charm_class)  # type: ignore
    # Use pathlib.Path(filename).parents[1] if tests structure is
    # <charm>/unit_tests
    # Use pathlib.Path(filename).parents[2] if tests structure is
    # <charm>/tests/unit/
    charm_dir = pathlib.Path(filename).parents[2]

    if not charm_metadata:
        metadata_file = f"{charm_dir}/charmcraft.yaml"
        if os.path.isfile(metadata_file):
            with open(metadata_file) as f:
                charm_metadata = f.read()

    if charm_metadata:
        loaded_metadata = yaml.safe_load(charm_metadata)

    if not charm_config and (config := loaded_metadata.get("config")):
        charm_config = yaml.safe_dump(config)

    if not charm_actions and (actions := loaded_metadata.get("actions")):
        charm_actions = yaml.safe_dump(actions)

    os.environ["JUJU_VERSION"] = "3.4.4"
    harness = Harness(
        charm_class,
        meta=charm_metadata,
        config=charm_config,
        actions=charm_actions,
    )
    harness._backend = _OSTestingModelBackend(
        harness._unit_name,
        harness._meta,
        harness._get_config(charm_config),
        JujuContext._from_dict(os.environ),
    )
    harness._model = model.Model(harness._meta, harness._backend)  # type: ignore
    harness._framework = framework.Framework(
        ops.storage.SQLiteStorage(":memory:"),
        harness._charm_dir,
        harness._meta,
        harness._model,
    )
    harness.set_model_name("test-model")

    return harness
