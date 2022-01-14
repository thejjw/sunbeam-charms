#!/usr/bin/env python3
"""Nova Operator Charm.

This charm provide Nova services as part of an OpenStack deployment
"""

import logging
import uuid
from typing import List

import ops.framework
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.container_handlers as sunbeam_chandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

logger = logging.getLogger(__name__)

NOVA_SCHEDULER_CONTAINER = "nova-scheduler"
NOVA_CONDUCTOR_CONTAINER = "nova-conductor"


class WSGINovaMetadataConfigContext(sunbeam_ctxts.ConfigContext):
    """Configuration context for WSGI configuration."""

    def context(self) -> dict:
        """WSGI configuration options."""
        log_svc_name = self.charm.service_name.replace('-', '_')
        return {
            "name": self.charm.service_name,
            "public_port": 8775,
            "user": self.charm.service_user,
            "group": self.charm.service_group,
            "wsgi_admin_script": '/usr/bin/nova-metadata-wsgi',
            "wsgi_public_script": '/usr/bin/nova-metadata-wsgi',
            "error_log": f"/var/log/apache2/{log_svc_name}_error.log",
            "custom_log": f"/var/log/apache2/{log_svc_name}_access.log",
        }


class NovaSchedulerPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):

    def get_layer(self):
        """Apache service

        :returns: pebble layer configuration for scheduler service
        :rtype: dict
        """
        return {
            "summary": "nova scheduler layer",
            "description": "pebble configuration for nova services",
            "services": {
                "nova-scheduler": {
                    "override": "replace",
                    "summary": "Nova Scheduler",
                    "command": "nova-scheduler",
                    "startup": "enabled"
                }
            }
        }

    def default_container_configs(self):
        return [
            sunbeam_core.ContainerConfigFile(
                [self.container_name],
                '/etc/nova/nova.conf',
                'nova',
                'nova')]


class NovaConductorPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):

    def get_layer(self):
        """Apache service

        :returns: pebble layer configuration for conductor service
        :rtype: dict
        """
        return {
            "summary": "nova conductor layer",
            "description": "pebble configuration for nova services",
            "services": {
                "nova-conductor": {
                    "override": "replace",
                    "summary": "Nova Conductor",
                    "command": "nova-conductor",
                    "startup": "enabled"
                }
            }
        }

    def default_container_configs(self):
        return [
            sunbeam_core.ContainerConfigFile(
                [self.container_name],
                '/etc/nova/nova.conf',
                'nova',
                'nova')]


class NovaOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "nova-api"
    wsgi_admin_script = '/usr/bin/nova-api-wsgi'
    wsgi_public_script = '/usr/bin/nova-api-wsgi'
    shared_metadata_secret_key = 'shared-metadata-secret'

    db_sync_cmds = [
        ['sudo', '-u', 'nova', 'nova-manage', 'api_db', 'sync'],
        ['sudo', '-u', 'nova', 'nova-manage', 'cell_v2', 'map_cell0'],
        ['sudo', '-u', 'nova', 'nova-manage', 'db', 'sync']]

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('public', self.default_public_ingress_port),
            ]
        )

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return f"/etc/nova/nova.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return 'nova'

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return 'nova'

    @property
    def service_endpoints(self):
        return [
            {
                'service_name': 'nova',
                'type': 'compute',
                'description': "OpenStack Compute",
                'internal_url': f'{self.internal_url}/v2.1',
                'public_url': f'{self.public_url}/v2.1',
                'admin_url': f'{self.admin_url}/v2.1'}]

    @property
    def default_public_ingress_port(self):
        return 8774

    @property
    def databases(self) -> List[str]:
        """Databases needed to support this charm.

        Need to override the default to specify three dbs.
        """
        return ["nova_api", "nova", "nova_cell0"]

    def get_pebble_handlers(self):
        pebble_handlers = super().get_pebble_handlers()
        pebble_handlers.extend([
            NovaSchedulerPebbleHandler(
                self,
                NOVA_SCHEDULER_CONTAINER,
                'nova-scheduler',
                [],
                self.template_dir,
                self.openstack_release,
                self.configure_charm),
            NovaConductorPebbleHandler(
                self,
                NOVA_CONDUCTOR_CONTAINER,
                'nova-conductor',
                [],
                self.template_dir,
                self.openstack_release,
                self.configure_charm)])
        return pebble_handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.extend(
            [
                WSGINovaMetadataConfigContext(
                    self, 'wsgi_nova_metadata',
                )
            ]
        )
        return _cadapters

    def get_shared_metadatasecret(self):
        """Return the shared metadata secret."""
        return self.leader_get(self.shared_metadata_secret_key)

    def set_shared_metadatasecret(self):
        """Store the shared metadata secret."""
        self.leader_set(
            {self.shared_metadata_secret_key: str(uuid.uuid1())})

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        metadata_secret = self.get_shared_metadatasecret()
        if metadata_secret:
            logging.debug("Found metadata secret in leader DB")
        else:
            if self.unit.is_leader():
                logging.debug("Creating metadata secret")
                self.set_shared_metadatasecret()
            else:
                logging.debug("Metadata secret not ready")
                return
        super().configure_charm(event)


class NovaWallabyOperatorCharm(NovaOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(NovaWallabyOperatorCharm, use_juju_for_storage=True)
