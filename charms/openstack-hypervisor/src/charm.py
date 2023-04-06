#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
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


"""OpenStack Hypervisor Operator Charm.

This charm provide hypervisor services as part of an OpenStack deployment
"""

import base64
import json
import logging
import secrets
import socket
import string
import subprocess
from typing import List

import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.ovn.relation_handlers as ovn_relation_handlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from netifaces import AF_INET, gateways, ifaddresses
import ops.framework
from ops.main import main

logger = logging.getLogger(__name__)


def _get_local_ip_by_default_route() -> str:
    """Get IP address of host associated with default gateway."""
    interface = "lo"
    ip = "127.0.0.1"

    # TOCHK: Gathering only IPv4
    if "default" in gateways():
        interface = gateways()["default"][AF_INET][1]

    ip_list = ifaddresses(interface)[AF_INET]
    if len(ip_list) > 0 and "addr" in ip_list[0]:
        ip = ip_list[0]["addr"]

    return ip


class HypervisorOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "hypervisor"
    METADATA_SECRET_KEY = "ovn-metadata-proxy-shared-secret"
    DEFAULT_SECRET_LENGTH = 32

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self._state.set_default(metadata_secret='')

    def get_relation_handlers(
        self, handlers: List[sunbeam_rhandlers.RelationHandler] = None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_relation_handlers.OVSDBCMSRequiresHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                "ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    def generate_metadata_secret(self) -> str:
        """Generate a secure secret.

        :param length: length of generated secret
        :type length: int
        :return: string containing the generated secret
        """
        return "".join(
            secrets.choice(string.ascii_letters + string.digits)
            for i in range(self.DEFAULT_SECRET_LENGTH)
        )

    def metadata_secret(self) -> str:
        """Retrieve or set self.METADATA_SECRET_KEY."""
        if self._state.metadata_secret:
            logging.debug("Found metadata secret in local db")
            return self._state.metadata_secret
        else:
            logging.debug("Generating new metadata secret")
            secret = self.generate_metadata_secret()
            self._state.metadata_secret = secret
            return secret

    def configure_unit(self, event) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready()
        config = self.model.config.get
        subprocess.check_call(
            [
                "snap",
                "install",
                "openstack-hypervisor",
                "--channel",
                config("snap-channel"),
            ]
        )
        local_ip = _get_local_ip_by_default_route()
        try:
            contexts = self.contexts()
            sb_connection_strs = list(contexts.ovsdb_cms.db_ingress_sb_connection_strs)
            if not sb_connection_strs:
                raise AttributeError(name='ovsdb southbound ingress string')
            snap_data = {
                "compute.cpu-mode": "host-model",
                "compute.spice-proxy-address": config("ip-address") or local_ip,
                "compute.virt-type": "kvm",
                "credentials.ovn-metadata-proxy-shared-secret": self.metadata_secret(),
                "identity.auth-url": contexts.identity_credentials.public_endpoint,
                "identity.password": contexts.identity_credentials.password,
                "identity.project-domain-name": contexts.identity_credentials.project_domain_name,
                "identity.project-name": contexts.identity_credentials.project_name,
                "identity.region-name": contexts.identity_credentials.region,
                "identity.user-domain-name": contexts.identity_credentials.user_domain_name,
                "identity.username": contexts.identity_credentials.username,
                "logging.debug": json.dumps(config("debug")),
                "network.dns-domain": config("dns-domain"),
                "network.dns-servers": config("dns-servers"),
                "network.enable-gateway": json.dumps(config("enable-gateway")),
                "network.external-bridge": config("external-bridge"),
                "network.external-bridge-address": config("external-bridge-address") or "10.20.20.1/24",
                "network.ip-address": config("ip-address") or local_ip,
                "network.ovn-key": base64.b64encode(
                    contexts.certificates.key.encode()
                ).decode(),
                "network.ovn-cert": base64.b64encode(
                    contexts.certificates.cert.encode()
                ).decode(),
                "network.ovn-cacert": base64.b64encode(
                    contexts.certificates.ca_cert.encode()
                ).decode(),
                "network.ovn-sb-connection": sb_connection_strs[0],
                "network.physnet-name": config("physnet-name"),
                "node.fqdn": config("fqdn") or socket.getfqdn(),
                "node.ip-address": config("ip-address") or local_ip,
                "rabbitmq.url": contexts.amqp.transport_url,
            }

            cmd = ["snap", "set", "openstack-hypervisor"] + [
                f"{k}={v}" for k, v in snap_data.items()
            ]
        except AttributeError as e:
            raise sunbeam_guard.WaitingExceptionError("Data missing: {}".format(e.name))
        subprocess.check_call(cmd)

        self._state.unit_bootstrapped = True


if __name__ == "__main__":  # pragma: no cover
    main(HypervisorOperatorCharm)
