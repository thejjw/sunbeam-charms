#!/usr/bin/env python3
#
# Copyright 2022 Canonical Ltd.
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
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
from ipaddress import (
    IPv4Address,
    IPv6Address,
)
from typing import (
    List,
    Mapping,
    Union,
)

import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.ovn.charm as ovn_charm
import ops_sunbeam.ovn.config_contexts as ovn_ctxts
import ops_sunbeam.ovn.container_handlers as ovn_chandlers
from charms.observability_libs.v0.kubernetes_service_patch import (
    KubernetesServicePatch,
)
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

OVSDB_SERVER = "ovsdb-server"


class OVNRelayPebbleHandler(ovn_chandlers.OVNPebbleHandler):
    """Handler for OVN Relay container."""

    @property
    def wrapper_script(self):
        """Wrapper script for managing OVN service."""
        return "/root/ovn-relay-wrapper.sh"

    @property
    def status_command(self):
        """Status command for container."""
        return "/usr/share/ovn/scripts/ovn-ctl status_ovsdb"

    @property
    def service_description(self):
        """Description of service."""
        return "OVN Relay"

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Initialise service ready for use.

        Write configuration files to the container and record
        that service is ready for us.

        NOTE: Override default to services being automatically started
        """
        self.setup_dirs()
        self.write_config(context)
        self.start_service()
        self._state.service_ready = True


class OVNRelayOperatorCharm(ovn_charm.OSBaseOVNOperatorCharm):
    """Charm the service."""

    _state = StoredState()
    mandatory_relations = {
        "ovsdb-cms",
        "certificates",
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ("southbound", 6642),
            ],
            service_type="LoadBalancer",
        )
        self.framework.observe(
            self.on.get_southbound_db_url_action,
            self._get_southbound_db_url_action,
        )

    def _get_southbound_db_url_action(self, event):
        event.set_results({"url": self.southbound_db_url})

    @property
    def ingress_address(self) -> Union[IPv4Address, IPv6Address]:
        """Network IP address for access to the OVN relay service."""
        return self.model.get_binding(
            "ovsdb-cms-relay"
        ).network.ingress_addresses[0]

    @property
    def southbound_db_url(self) -> str:
        """Full connection URL for Southbound DB relay."""
        return f"ssl:{self.ingress_address}:6642"

    def get_pebble_handlers(self):
        """Return pebble handler for container."""
        pebble_handlers = [
            OVNRelayPebbleHandler(
                self,
                OVSDB_SERVER,
                "ovn-relay",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]
        return pebble_handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(ovn_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Return empty dict as no mysql databases are
        required.
        """
        return {}


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OVNRelayOperatorCharm, use_juju_for_storage=True)
