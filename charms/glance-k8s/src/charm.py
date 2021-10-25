#!/usr/bin/env python3
"""Glance Operator Charm.

This charm provide Glance services as part of an OpenStack deployment
"""

import logging
from typing import List

from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.cprocess as sunbeam_cprocess
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

    def _do_bootstrap(self):
        """
        Starts the appropriate services in the order they are needed.
        If the service has not yet been bootstrapped, then this will
         1. Create the database
        """
        super()._do_bootstrap()
        try:
            container = self.unit.get_container(self.wsgi_container_name)
            logger.info("Syncing database...")
            out = sunbeam_cprocess.check_output(
                container,
                [
                    'sudo', '-u', 'glance',
                    'glance-manage', '--config-dir',
                    '/etc/glance', 'db', 'sync'],
                service_name='keystone-db-sync',
                timeout=180)
            logging.debug(f'Output from database sync: \n{out}')
        except sunbeam_cprocess.ContainerProcessError:
            logger.exception('Failed to bootstrap')
            self._state.bootstrapped = False
            return

    def configure_charm(self, event) -> None:
        """Catchall handler to cconfigure charm services."""
        if not self.relation_handlers_ready():
            logging.debug("Defering configuration, charm relations not ready")
            return

        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                container = self.unit.get_container(
                    ph.container_name
                )
                sunbeam_cprocess.check_call(
                    container,
                    ['apt', 'update'])
                sunbeam_cprocess.check_call(
                    container,
                    ['apt', 'install', '-y', 'ceph-common'])
                try:
                    sunbeam_cprocess.check_call(
                        container,
                        ['ceph-authtool',
                         f'/etc/ceph/ceph.client.{self.app.name}.keyring',
                         '--create-keyring',
                         f'--name=client.{self.app.name}',
                         f'--add-key={self.ceph.key}']
                    )
                except sunbeam_cprocess.ContainerProcessError:
                    pass
                ph.init_service(self.contexts())

        super().configure_charm(event)
        # Restarting services after bootstrap should be in aso
        if self._state.bootstrapped:
            for handler in self.pebble_handlers:
                handler.start_service()


class GlanceWallabyOperatorCharm(GlanceOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(GlanceWallabyOperatorCharm, use_juju_for_storage=True)
