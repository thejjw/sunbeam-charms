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
from typing import List

from ops.framework import StoredState
from ops.main import main

import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.ovn.container_handlers as ovn_chandlers
import ops_sunbeam.ovn.config_contexts as ovn_ctxts
import ops_sunbeam.ovn.charm as ovn_charm

logger = logging.getLogger(__name__)

OVSDB_SERVER = "ovsdb-server"


class OVNRelayPebbleHandler(ovn_chandlers.OVNPebbleHandler):

    @property
    def wrapper_script(self):
        return '/root/ovn-relay-wrapper.sh'

    @property
    def service_description(self):
        return 'OVN Relay'


class OVNRelayOperatorCharm(ovn_charm.OSBaseOVNOperatorCharm):
    """Charm the service."""

    _state = StoredState()

    def get_pebble_handlers(self):
        pebble_handlers = [
            OVNRelayPebbleHandler(
                self,
                OVSDB_SERVER,
                'ovn-relay',
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm)]
        return pebble_handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            ovn_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts


class OVNRelayXenaOperatorCharm(OVNRelayOperatorCharm):

    openstack_release = 'xena'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OVNRelayXenaOperatorCharm, use_juju_for_storage=True)
