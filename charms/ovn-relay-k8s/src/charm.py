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

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.container_handlers as sunbeam_chandlers
import advanced_sunbeam_openstack.core as sunbeam_core

import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

logger = logging.getLogger(__name__)

OVSDB_SERVER = "ovsdb-server"


class OVNRelayPebbleHandler(sunbeam_chandlers.OVNPebbleHandler):

    @property
    def wrapper_script(self):
        return '/root/ovn-relay-wrapper.sh'

    @property
    def service_description(self):
        return 'OVN Relay'


class OVNRelayOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = StoredState()

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('southboundrelay', 6642),
            ]
        )
        self.configure_charm(None)

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
            sunbeam_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts


class OVNRelayWallabyOperatorCharm(OVNRelayOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OVNRelayWallabyOperatorCharm, use_juju_for_storage=True)
