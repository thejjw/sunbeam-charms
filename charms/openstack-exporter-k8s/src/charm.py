#!/usr/bin/env python3
"""Openstack-exporter Operator Charm.

This charm provide Openstack-exporter services as part of an OpenStack deployment
"""

import hashlib
import json
import logging
from typing import TYPE_CHECKING, List, Optional

import ops
import pwgen
from ops.main import main

import charms.keystone_k8s.v0.identity_resource as identity_resource
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_config_contexts
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers


logger = logging.getLogger(__name__)

CREDENTIALS_SECRET_PREFIX = "credentials_"
CONTAINER = "openstack-exporter"


class OSExporterConfigurationContext(sunbeam_config_contexts.ConfigContext):
    """OSExporter configuration context."""

    if TYPE_CHECKING:
        charm: "OSExporterOperatorCharm"

    @property
    def ready(self) -> bool:
        """Whether the context has all the data is needs."""
        return self.charm.auth_url is not None

    def context(self) -> dict:
        """OS Exporter configuration context."""
        username, password = self.charm.user_credentials
        return {
            "domain_name": self.charm.domain,
            "project_name": self.charm.project,
            "username": username,
            "password": password,
            "auth_url": self.charm.auth_url,
        }


class OSExporterPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    def get_layer(self) -> dict:
        """Pebble configuration layer for the container."""
        return {
            "summary": "openstack-exporter service",
            "description": ("Pebble config layer for openstack-exporter"),
            "services": {
                self.service_name: {
                    "override": "replace",
                    "summary": "Openstack-Exporter",
                    "command": (
                        "openstack-exporter"
                        " --os-client-config /etc/os-exporter/clouds.yaml"
                        " --multi-cloud"
                    ),
                    "startup": "disabled",
                },
            },
        }


class OSExporterOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    mandatory_relations = {
        # "certificates",
        "identity-ops",
    }
    service_name = "openstack-exporter"

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the operator."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/os-exporter/clouds.yaml",
                "_daemon_",
                "_daemon_",
            ),
            # sunbeam_core.ContainerConfigFile(
            #     "/etc/ssl/ca.pem",
            #     "_daemon_",
            #     "_daemon_",
            # ),
        ]

    @property
    def config_contexts(self) -> List[sunbeam_config_contexts.ConfigContext]:
        """Generate list of configuration adapters for the charm."""
        _cadapters = super().config_contexts
        _cadapters.append(OSExporterConfigurationContext(self, "os_exporter"))
        return _cadapters

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/os-exporter/clouds.yaml"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "_daemon_"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "_daemon_"

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 9180

    @property
    def os_exporter_user(self) -> str:
        """User for openstack-exporter."""
        return "openstack-exporter"

    @property
    def domain(self):
        """Domain name for openstack-exporter."""
        return "default"

    @property
    def project(self):
        """Project name for openstack-exporter."""
        return "services"

    @property
    def user_credentials(self) -> tuple:
        """Credentials for domain admin user."""
        credentials_id = self._get_os_exporter_credentials_secret()
        credentials = self.model.get_secret(id=credentials_id)
        username = credentials.get_content().get("username")
        user_password = credentials.get_content().get("password")
        return (username, user_password)

    @property
    def auth_url(self) -> Optional[str]:
        """Auth url for openstack-exporter."""
        for op in self.id_ops.interface.response.get("ops"):
            if op.get("name") != "list_endpoint":
                continue
            for endpoint in op.get("value", []):
                return endpoint.get("url")
        return None

    def get_relation_handlers(self) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        self.id_ops = sunbeam_rhandlers.IdentityResourceRequiresHandler(
            self,
            "identity-ops",
            self.handle_keystone_ops,
            mandatory="identity-ops" in self.mandatory_relations,
        )
        handlers.append(self.id_ops)
        return handlers

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for operator."""
        return [
            OSExporterPebbleHandler(
                self,
                CONTAINER,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]

    def hash_ops(self, ops: list) -> str:
        """Return the sha1 of the requested ops."""
        return hashlib.sha1(json.dumps(ops).encode()).hexdigest()

    def _grant_os_exporter_credentials_secret(
        self,
        relation: ops.Relation,
    ) -> None:
        """Grant secret access to the related units."""
        credentials_id = None
        try:
            credentials_id = self._get_os_exporter_credentials_secret()
            secret = self.model.get_secret(id=credentials_id)
            logger.debug(
                f"Granting access to secret {credentials_id} for relation "
                f"{relation.app.name} {relation.name}/{relation.id}"
            )
            secret.grant(relation)
        except (ops.ModelError, ops.SecretNotFoundError) as e:
            logger.debug(
                f"Error during granting access to secret {credentials_id} for "
                f"relation {relation.app.name} {relation.name}/{relation.id}: "
                f"{str(e)}"
            )

    def _retrieve_or_set_secret(
        self,
        username: str,
        rotate: ops.SecretRotate = ops.SecretRotate.NEVER,
        add_suffix_to_username: bool = False,
    ) -> str:
        """Retrieve or create a secret."""
        label = f"{CREDENTIALS_SECRET_PREFIX}{username}"
        credentials_id = self.peers.get_app_data(label)
        if credentials_id:
            return credentials_id

        password = str(pwgen.pwgen(12))
        if add_suffix_to_username:
            suffix = pwgen.pwgen(6)
            username = f"{username}-{suffix}"
        credentials_secret = self.model.app.add_secret(
            {"username": username, "password": password},
            label=label,
            rotate=rotate,
        )
        self.peers.set_app_data(
            {
                label: credentials_secret.id,
            }
        )
        return credentials_secret.id

    def _get_os_exporter_credentials_secret(self) -> str:
        """Get domain admin secret."""
        label = f"{CREDENTIALS_SECRET_PREFIX}{self.os_exporter_user}"
        credentials_id = self.peers.get_app_data(label)

        if not credentials_id:
            credentials_id = self._retrieve_or_set_secret(
                self.os_exporter_user,
            )

        return credentials_id

    def _get_os_exporter_user_ops(self) -> list:
        """Generate ops request for domain setup."""
        credentials_id = self._get_os_exporter_credentials_secret()
        ops = [
            # show domain default
            {
                "name": "show_domain",
                "params": {"name": "default"},
            },
            # fetch keystone endpoint
            {
                "name": "list_endpoint",
                "params": {"name": "keystone", "interface": "admin"},
            },
            # Create user openstack exporter
            {
                "name": "create_user",
                "params": {
                    "name": self.os_exporter_user,
                    "password": credentials_id,
                    "domain": "default",
                },
            },
            # check with reader system scoped permissions
        ]
        return ops

    def _handle_initial_os_exporter_user_setup_response(
        self,
        event: ops.RelationEvent,
    ) -> None:
        """Handle domain setup response from identity-ops."""
        if {
            op.get("return-code")
            for op in self.id_ops.interface.response.get(
                "ops",
                [],
            )
        } == {0}:
            logger.debug(
                "Initial openstack exporter user setup commands completed,"
                " running configure charm"
            )
            self.configure_charm(event)

    def handle_keystone_ops(self, event: ops.RelationEvent) -> None:
        """Event handler for identity ops."""
        if isinstance(event, identity_resource.IdentityOpsProviderReadyEvent):
            self._state.identity_ops_ready = True

            if not self.unit.is_leader():
                return

            # Send op request only by leader unit
            ops = self._get_os_exporter_user_ops()
            id_ = self.hash_ops(ops)
            self._grant_os_exporter_credentials_secret(event.relation)
            request = {
                "id": id_,
                "tag": "initial_openstack_exporter_user_setup",
                "ops": ops,
            }
            logger.debug(f"Sending ops request: {request}")
            self.id_ops.interface.request_ops(request)
        elif isinstance(
            event,
            identity_resource.IdentityOpsProviderGoneAwayEvent,
        ):
            self._state.identity_ops_ready = False
        elif isinstance(event, identity_resource.IdentityOpsResponseEvent):
            if not self.unit.is_leader():
                return
            response = self.id_ops.interface.response
            logger.debug(f"Got response from keystone: {response}")
            request_tag = response.get("tag")
            if request_tag == "initial_openstack_exporter_user_setup":
                self._handle_initial_os_exporter_user_setup_response(event)


if __name__ == "__main__":
    main(OSExporterOperatorCharm)
