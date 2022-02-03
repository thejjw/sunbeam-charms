#!/usr/bin/env python3
"""Glance Operator Charm.

This charm provide Glance services as part of an OpenStack deployment
"""

import logging
from typing import List

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

logger = logging.getLogger(__name__)


class GlanceOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    ceph_conf = "/etc/ceph/ceph.conf"

    _state = StoredState()
    _authed = False
    service_name = "glance-api"
    wsgi_admin_script = '/usr/bin/glance-wsgi-api'
    wsgi_public_script = '/usr/bin/glance-wsgi-api'

    db_sync_cmds = [
        ['sudo', '-u', 'glance', 'glance-manage', '--config-dir',
         '/etc/glance', 'db', 'sync']]

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('public', self.default_public_ingress_port),
            ]
        )

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            sunbeam_ctxts.CephConfigurationContext(self, "ceph_config"))
        contexts.append(
            sunbeam_ctxts.CinderCephConfigurationContext(self, "cinder_ceph"))
        return contexts

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for the operator."""
        _cconfigs = super().container_configs
        _cconfigs.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    [self.service_name],
                    self.ceph_conf,
                    self.service_user,
                    self.service_group,
                ),
            ]
        )
        return _cconfigs

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.ceph = sunbeam_rhandlers.CephClientHandler(
            self,
            "ceph",
            self.configure_charm,
            allow_ec_overwrites=True,
            app_name='rbd'
        )
        handlers.append(self.ceph)
        return handlers

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/glance/glance-api.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'glance'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'glance'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'glance',
                'type': 'image',
                'description': "OpenStack Image",
                'internal_url': f'{self.internal_url}',
                'public_url': f'{self.public_url}',
                'admin_url': f'{self.admin_url}'}]

    @property
    def default_public_ingress_port(self):
        return 9292

    def configure_charm(self, event) -> None:
        """Catchall handler to cconfigure charm services."""
        if not self.relation_handlers_ready():
            logging.debug("Defering configuration, charm relations not ready")
            return
        if not self.ceph.key:
            logging.debug("No Ceph key found")
            return

        ph = self.get_named_pebble_handler("glance-api")
        if ph.pebble_ready:
            ph.execute(
                ['apt', 'update'],
                exception_on_error=True)
            ph.execute(
                ['apt', 'install', '-y', 'ceph-common'],
                exception_on_error=True)
            ph.execute(
                [
                    'ceph-authtool',
                    f'/etc/ceph/ceph.client.{self.app.name}.keyring',
                    '--create-keyring',
                    f'--name=client.{self.app.name}',
                    f'--add-key={self.ceph.key}'],
                exception_on_error=True)
            ph.init_service(self.contexts())

        super().configure_charm(event)
        if self._state.bootstrapped:
            for handler in self.pebble_handlers:
                handler.start_service()


class GlanceWallabyOperatorCharm(GlanceOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(GlanceWallabyOperatorCharm, use_juju_for_storage=True)
