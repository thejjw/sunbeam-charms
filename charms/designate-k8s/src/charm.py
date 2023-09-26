#!/usr/bin/env python3
#
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
#
#
# Learn more at: https://juju.is/docs/sdk

"""Designate Operator Charm.

This charm provide Designate services as part of an OpenStack deployment
"""

import logging
import secrets
from typing import (
    Callable,
    List,
    Mapping,
    Optional,
)

import charms.designate_bind_k8s.v0.bind_rndc as bind_rndc
import ops
import ops.charm
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import tenacity
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

DESIGNATE_CONTAINER = "designate"
BIND_RNDC_RELATION = "dns-backend"
RNDC_SECRET_PREFIX = "rndc_"
NONCE_SECRET_LABEL = "nonce-rndc"


class NoRelationError(Exception):
    """No relation found."""

    pass


class DesignatePebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Pebble handler for designate services."""

    _common_service_config = {
        "override": "replace",
        "user": "designate",
        "group": "designate",
    }

    def __init__(
        self,
        charm: ops.CharmBase,
        container_name: str,
        template_dir: str,
        callback_f: Callable,
    ) -> None:
        self.wsgi_service_name = "wsgi-designate-api"
        super().__init__(
            charm,
            container_name,
            "designate",
            [],
            template_dir,
            callback_f,
            self.wsgi_service_name,
        )

    @property
    def wsgi_conf(self) -> str:
        """Location of WSGI config file."""
        return f"/etc/apache2/sites-available/{self.wsgi_service_name}.conf"

    def get_layer(self) -> dict:
        """Designate service layer."""
        layer = super().get_layer()

        # all designate services added to the same container
        # to prevent massive slow down in hook execution
        layer["services"].update(
            {
                "designate-central": {
                    "summary": "designate central",
                    "command": "designate-central",
                    **self._common_service_config,
                },
                "designate-worker": {
                    "summary": "designate worker",
                    "command": "designate-worker",
                    **self._common_service_config,
                },
                "designate-producer": {
                    "summary": "designate producer",
                    "command": "designate-producer",
                    **self._common_service_config,
                },
                "designate-mdns": {
                    "summary": "designate mdns",
                    "command": "designate-mdns",
                    **self._common_service_config,
                },
            }
        )
        return layer

    def default_container_configs(
        self,
    ) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configurations for handler."""
        _cconfig = super().default_container_configs()
        _cconfig.extend(
            [
                sunbeam_core.ContainerConfigFile(
                    "/etc/designate/designate.conf",
                    "designate",
                    "designate",
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/designate/pools.yaml",
                    "designate",
                    "designate",
                ),
                sunbeam_core.ContainerConfigFile(
                    "/etc/designate/rndc.key",
                    "designate",
                    "designate",
                ),
            ]
        )
        return _cconfig

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(
                ["a2dissite", "000-default"], timeout=5 * 60
            )
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2dissite warn: %s", line.strip())
            logger.debug(f"Output from a2dissite: \n{out}")
        except ops.pebble.ExecError:
            logger.exception("Failed to disable '000-default' site in apache")
        super().init_service(context)


class BindRndcRequiresRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler class."""

    charm: "DesignateOperatorCharm"
    interface: bind_rndc.BindRndcRequires

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = True,
    ):
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> ops.Object:
        """Setup event handler for the relation."""
        interface = bind_rndc.BindRndcRequires(self.charm, BIND_RNDC_RELATION)
        self.framework.observe(
            interface.on.connected,
            self._on_bind_rndc_connected,
        )
        self.framework.observe(
            interface.on.ready,
            self._on_bind_rndc_ready,
        )
        self.framework.observe(
            interface.on.goneaway,
            self._on_bind_rndc_goneaway,
        )

        try:
            self.request_rndc_key(interface, self._relation)
        except NoRelationError:
            pass

        return interface

    def _on_bind_rndc_connected(self, event: bind_rndc.BindRndcConnectedEvent):
        """Handle bind rndc connected event."""
        relation = self.model.get_relation(
            event.relation_name, event.relation_id
        )
        if relation is not None:
            self.request_rndc_key(self.interface, relation)

    def _on_bind_rndc_ready(self, event: bind_rndc.BindRndcReadyEvent):
        """Handle bind rndc ready event."""
        self.callback_f(event)

    def _on_bind_rndc_goneaway(self, event: bind_rndc.BindRndcGoneAwayEvent):
        """Handle bind rndc goneaway event."""
        self.callback_f(event)

    def request_rndc_key(
        self, interface: bind_rndc.BindRndcRequires, relation: ops.Relation
    ):
        """Request credentials from vault-kv relation."""
        nonce = self.charm.get_nonce()
        if nonce is None:
            return
        interface.request_rndc_key(relation, nonce)

    @property
    def _relation(self) -> ops.Relation:
        """Get relation."""
        relation = self.framework.model.get_relation(self.relation_name)
        if relation is None:
            raise NoRelationError("Relation not found")
        return relation

    @property
    def ready(self) -> bool:
        """Ready when a key is available for current unit."""
        try:
            relation = self._relation
            return self.interface.get_rndc_key(relation) is not None
        except Exception:
            return False

    @property
    def rndc_key(self) -> dict:
        """Return current's unit rndc key which secret rendered."""
        rndc_key_secret = self.interface.get_rndc_key(self._relation)
        if rndc_key_secret is None:
            raise sunbeam_guard.BlockedExceptionError("Rndc missing")

        unit_name = self.charm.unit.name.replace("/", "-")
        rndc_key = rndc_key_secret.copy()
        secret = self.charm.model.get_secret(
            id=rndc_key["secret"], label=RNDC_SECRET_PREFIX + unit_name
        )
        secret_value = secret.get_content()["secret"]
        rndc_key["secret"] = secret_value
        rndc_key["name"] = self.interface.nonce(self._relation)

        return rndc_key

    @property
    def bind_host(self) -> str:
        """Get bind host ip."""
        host = self.interface.host(self._relation)
        if host is None:
            raise sunbeam_guard.BlockedExceptionError("Host missing")
        return host

    @property
    def mdns_ip(self) -> str:
        """Get mdns ip.

        Return the ingress address, on a K8S cloud this will be IP address
        from the service definition, such as a ClusterIP or a LoadBalancer.

        Related bind will use this address to transfer zones for example.
        """
        binding = self.model.get_binding(self._relation)
        if binding is None:
            raise Exception("Binding not found")
        return str(binding.network.ingress_address)

    def context(self) -> dict:
        """Render context needed for jinja templating."""
        return {
            "rndc_key": self.rndc_key,
            "host": self.bind_host,
            "mdns_ip": self.mdns_ip,
            "rndc_file_key": "/etc/designate/rndc.key",
            "ns_records": self.charm.ns_records,
        }


class DesignateOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = ops.StoredState()
    service_name = "designate"
    wsgi_admin_script = "/usr/bin/designate-api-wsgi"
    wsgi_public_script = "/usr/bin/designate-api-wsgi"

    db_sync_cmds = [
        ["sudo", "-u", "designate", "designate-manage", "database", "sync"],
    ]
    pool_sync_cmds = [
        ["sudo", "-u", "designate", "designate-manage", "pool", "update"],
    ]

    mandatory_relations = {
        "database",
        "identity-service",
        "ingress-public",
        "amqp",
        BIND_RNDC_RELATION,
    }

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)

    def _on_install(self, event: ops.EventBase) -> None:
        """Handle install event."""
        self.unit.add_secret(
            {"nonce": secrets.token_hex(16)},
            label=NONCE_SECRET_LABEL,
            description="nonce for bind-rndc relation",
        )

    def configure_unit(self, event: ops.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready()
        self.open_ports()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        self.run_db_sync()
        self.run_pool_update()
        self._state.unit_bootstrapped = True

    def open_ports(self):
        """Register ports in underlying cloud."""
        super().open_ports()
        self.unit.open_port("tcp", 5354)  # mdns port

    def get_pebble_handlers(
        self,
    ) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for operator."""
        return [
            DesignatePebbleHandler(
                self,
                DESIGNATE_CONTAINER,
                self.template_dir,
                self.configure_charm,
            ),
        ]

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler(BIND_RNDC_RELATION, handlers):
            self.bind_rndc = BindRndcRequiresRelationHandler(
                self,
                BIND_RNDC_RELATION,
                self.configure_charm,
                mandatory=BIND_RNDC_RELATION in self.mandatory_relations,
            )
            handlers.append(self.bind_rndc)

        return super().get_relation_handlers(handlers)

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Need to override the default
        because we're registering multiple databases.
        """
        return {
            "database": "designate",
            "shared-database": "designate_shared",
        }

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/designate/designate.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "designate"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "designate"

    @property
    def service_endpoints(self):
        """Return service endpoints for the service."""
        return [
            {
                "service_name": "designate",
                "type": "dns",
                "description": "OpenStack Designate API",
                "internal_url": self.internal_url,
                "public_url": self.public_url,
                "admin_url": self.admin_url,
            }
        ]

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 9001

    @property
    def ns_records(self) -> List[str]:
        """Get nameserver records."""
        nameservers = self.config.get("nameservers")
        if nameservers is None:
            return []
        return nameservers.split(" ")

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        retry=(
            tenacity.retry_if_exception_type(ops.pebble.ChangeError)
            | tenacity.retry_if_exception_type(ops.pebble.ExecError)
        ),
        after=tenacity.after_log(logger, logging.WARNING),
        wait=tenacity.wait_exponential(multiplier=1, min=10, max=300),
    )
    def _retry_pool_update(self, cmd):
        container = self.unit.get_container(DESIGNATE_CONTAINER)
        logger.debug("Running pool update: \n%s", cmd)
        process = container.exec(cmd, timeout=5 * 60)
        out, warnings = process.wait_output()
        if warnings:
            logger.debug("Pool update stdout: \n%s", out)
            for line in warnings.splitlines():
                logger.warning("Pool update stderr: \n%s", line.strip())

    def run_pool_update(self) -> None:
        """Update designate pools.

        :raises: pebble.ExecError
        """
        if not self.unit.is_leader():
            logger.info("Not lead unit, skipping pool update")
            return
        logger.info("Updating pools...")

        for cmd in self.pool_sync_cmds:
            try:
                self._retry_pool_update(cmd)
            except tenacity.RetryError:
                raise sunbeam_guard.BlockedExceptionError(
                    "Updating pools failed"
                )

    def get_nonce(self) -> Optional[str]:
        """Return nonce stored in secret."""
        try:
            secret = self.model.get_secret(label=NONCE_SECRET_LABEL)
            return secret.get_content()["nonce"]
        except ops.SecretNotFoundError:
            return None


if __name__ == "__main__":
    main(DesignateOperatorCharm)
