#!/usr/bin/env python3
# Copyright 2022 liam
# See LICENSE file for licensing details.
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

import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts
import advanced_sunbeam_openstack.ovn.container_handlers as ovn_chandlers
import advanced_sunbeam_openstack.ovn.config_contexts as ovn_ctxts
import advanced_sunbeam_openstack.ovn.charm as ovn_charm

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


class OVNRelayWallabyOperatorCharm(OVNRelayOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OVNRelayWallabyOperatorCharm, use_juju_for_storage=True)
