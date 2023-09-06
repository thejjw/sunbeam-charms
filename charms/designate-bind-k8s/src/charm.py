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
"""Bind9 Operator Charm.

This charm provide Bind9 services
"""

import base64
import hashlib
import hmac
import logging
import secrets
from typing import (
    Callable,
    List,
)

import charms.bind9_k8s.v0.bind_rndc as bind_rndc
import ops.charm
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)

logger = logging.getLogger(__name__)

BIND_RNDC_RELATION = "dns-backend"
RNDC_SECRET_PREFIX = "rndc_"
RNDC_REVISION_KEY = "rndc_revision"


class BindPebbleHandler(sunbeam_chandlers.ServicePebbleHandler):
    """Pebble handler for bind9 service."""

    def get_layer(self) -> dict:
        """Pebble layer for bind 9 service."""
        return {
            "summary": "bind9 layer",
            "description": "pebble config layer for bind9",
            "services": {
                "bind9": {
                    "override": "replace",
                    "summary": "bind9",
                    "command": "/usr/sbin/named -g -u bind",
                    "startup": "enabled",
                }
            },
        }


class BindRndcProvidesRelationHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for managing rndc clients."""

    interface: bind_rndc.BindRndcProvides

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
        mandatory: bool = True,
    ):
        super().__init__(charm, relation_name, callback_f, mandatory)

    def setup_event_handler(self) -> ops.Object:
        """Setup event handler for the relation."""
        interface = bind_rndc.BindRndcProvides(self.charm, BIND_RNDC_RELATION)
        self.framework.observe(
            interface.on.bind_client_updated,
            self._on_bind_client_updated,
        )
        return interface

    def _on_bind_client_updated(self, event: bind_rndc.BindClientUpdatedEvent):
        """Handle bind client updated event."""
        self.callback_f(event)

    @property
    def _relations(self) -> list[ops.Relation]:
        """Get relations."""
        return self.model.relations[self.relation_name]

    def keys(self, rndc_keys: dict[str, dict[str, str]]) -> str:
        """Get rndc keys formatted for named.conf allowed keys.

        Format is "key1";"key2";"key3";
        """
        return '"' + '";"'.join(rndc_keys.keys()) + '";'

    @property
    def rndc_keys(self) -> dict:
        """Get rndc keys from relations with secret rendered."""
        rndc_keys = {}
        for relation in self._relations:
            if relation.app is None:
                logger.debug(
                    "No remote app found for relation %r:%r,"
                    " skipping rendering rndc_keys",
                    relation.name,
                    str(relation.id),
                )
                continue

            rndc_keys_secret = self.interface.get_rndc_keys(relation)
            rndc_keys_current = {}
            for name, value in rndc_keys_secret.items():
                secret = self.charm.model.get_secret(id=value["secret"])
                key_value = secret.get_content()["secret"]
                name = relation.name + ":" + str(relation.id) + "_" + name
                rndc_keys_current[name] = value
                rndc_keys_current[name]["secret"] = key_value
            rndc_keys.update(rndc_keys_current)

        return rndc_keys

    @property
    def ready(self) -> bool:
        """Determine with the relation is ready for use."""
        try:
            return len(self._relations) > 0
        except Exception:
            return False

    def context(self) -> dict:
        """Context containing the relation data to render."""
        rndc_keys = self.rndc_keys
        return {
            "rndc_keys": rndc_keys,
            "keys": self.keys(rndc_keys),
        }


class BindOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    _state = StoredState()
    service_name = "bind9"

    # mandatory_relations = {}

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.secret_rotate, self._on_secret_rotate)

    def _on_secret_rotate(self, event: ops.SecretRotateEvent):
        """Handle secret rotate event."""
        if not self.unit.is_leader():
            logger.debug("Not leader, skipping secret rotate")
            return
        if event.secret.label is None:
            logger.debug("Secret %r has no label, skipping", event.secret.id)
            return
        if event.secret.label.startswith(RNDC_SECRET_PREFIX):
            event.secret.set_content({"secret": self.generate_rndc_key()})
            self.leader_set({RNDC_REVISION_KEY: self.new_rndc_revision()})
            self.configure_charm(event)

    @property
    def container_configs(self) -> List[sunbeam_core.ContainerConfigFile]:
        """Container configuration files for the operator."""
        return [
            sunbeam_core.ContainerConfigFile(
                "/etc/bind/named.conf",
                "root",
                "bind",
            ),
            sunbeam_core.ContainerConfigFile(
                "/etc/bind/named.conf.options",
                "root",
                "bind",
            ),
        ]

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the operator."""
        return [
            BindPebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            )
        ]

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler(BIND_RNDC_RELATION, handlers):
            self.bind_rndc = BindRndcProvidesRelationHandler(
                self,
                BIND_RNDC_RELATION,
                self.register_rndc_client_from_event,
                BIND_RNDC_RELATION in self.mandatory_relations,
            )
            handlers.append(self.bind_rndc)

        return super().get_relation_handlers(handlers)

    @property
    def service_conf(self) -> str:
        """Service default configuration file."""
        return "/etc/bind/named.conf"

    @property
    def service_user(self) -> str:
        """Service user file and directory ownership."""
        return "bind"

    @property
    def service_group(self) -> str:
        """Service group file and directory ownership."""
        return "bind"

    @property
    def default_public_ingress_port(self):
        """Ingress Port for API service."""
        return 53

    @property
    def rndc_algorithm(self) -> str:
        """Algorithm used to encode rndc secret.

        :returns: str: Algorithm used to encode rndc secret
        """
        return "hmac-sha256"

    def open_ports(self):
        """Register ports in underlying cloud."""
        self.unit.open_port("udp", self.default_public_ingress_port)
        self.unit.open_port("tcp", 953)  # rndc port

    def can_service_requests(self) -> bool:
        """Check if unit can process client requests."""
        if self.bootstrapped() and self.unit.is_leader():
            logger.debug("Can service client requests")
            return True
        else:
            logger.debug(
                "Cannot service client requests. "
                "Bootstrapped: {} Leader {}".format(
                    self.bootstrapped(), self.unit.is_leader()
                )
            )
            return False

    def generate_rndc_key(self) -> str:
        """Generate rndc key."""
        key = secrets.token_bytes(10)
        dig = hmac.new(
            key, msg=b"RNDC Secret", digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()

    def register_rndc_client_from_event(
        self,
        event: bind_rndc.NewBindClientAttachedEvent,
    ):
        """Register rndc client from event."""
        if self.can_service_requests():
            any_change = self.register_rndc_client(
                event.relation_name, event.relation_id
            )
            any_change |= self.cleanup_rndc_clients(
                event.relation_name, event.relation_id
            )
            if any_change:
                self.configure_charm(event)
                if self.unit.is_leader():
                    self.leader_set(
                        {RNDC_REVISION_KEY: self.new_rndc_revision()}
                    )

    def new_rndc_revision(self) -> str:
        """Compute new revision for rndc keys."""
        revision = self.leader_get(RNDC_REVISION_KEY)
        if revision is None:
            revision = 0
        else:
            revision = int(revision)
        return str(revision + 1)

    def register_rndc_client(
        self, relation_name: str, relation_id: int
    ) -> bool:
        """Register rndc client."""
        if not self.unit.is_leader():
            logger.debug("Not leader, skipping register_rndc_client")
            return False

        logger.debug(
            "Registering rndc client on relation %s %d",
            relation_name,
            relation_id,
        )
        relation = self.framework.model.get_relation(
            relation_name, relation_id
        )
        if relation is None:
            raise

        keys = self.bind_rndc.interface.get_rndc_keys(relation)
        any_change = False
        for unit in relation.units:
            unit_name = unit.name.replace("/", "-")
            nonce = relation.data[unit].get("nonce")
            if nonce is None:
                logger.debug("No nonce found for %s, skipping", unit.name)
                continue
            if nonce in keys:
                logger.debug(
                    "Client %s already registered, skipping", unit.name
                )
                continue
            any_change = True
            secret = self._create_or_update_secret(
                RNDC_SECRET_PREFIX + unit_name,
                {"secret": self.generate_rndc_key()},
                relation,
            )
            self.bind_rndc.interface.set_rndc_client_key(
                relation, nonce, self.rndc_algorithm, secret
            )
        return any_change

    def _create_or_update_secret(
        self,
        label: str,
        content: dict[str, str],
        relation: ops.Relation | None = None,
    ) -> ops.Secret:
        """Create or update a secret.

        Registers the secret label and id in the peer relation.
        """
        if not self.unit.is_leader():
            raise Exception("Can only create the secret on the leader unit.")
        id = self.leader_get(label)
        if id is None:
            secret = self.app.add_secret(
                content,
                label=label,
                rotate=ops.SecretRotate.MONTHLY,
            )
            self.leader_set({label: secret.id})
        else:
            secret = self.model.get_secret(id=id)
            secret.set_content(content)
        if relation is not None:
            secret.grant(relation)
        return secret

    def cleanup_rndc_clients(
        self, relation_name: str, relation_id: int
    ) -> bool:
        """Cleanup rndc clients.

        When a unit is upgraded the nonce will change.
        Remove older rndc keys that are not used anymore.

        This method compares rndc keys with unit's nonces.
        """
        if not self.unit.is_leader():
            logger.debug("Not leader, skipping cleanup_rndc_clients")
            return False
        logger.debug(
            "Cleaning up rndc clients on relation %s %d",
            relation_name,
            relation_id,
        )
        relation = self.framework.model.get_relation(
            relation_name, relation_id
        )
        if relation is None:
            raise
        rndc_keys = self.bind_rndc.interface.get_rndc_keys(relation)
        nonces = []
        for unit in relation.units:
            nonce = relation.data[unit].get("nonce")
            if nonce is not None:
                nonces.append(nonce)

        missing_nonces = list(set(rndc_keys.keys()) - set(nonces))
        logger.debug("Missing nonces: %r", missing_nonces)
        self.bind_rndc.interface.remove_rndc_client_key(
            relation, missing_nonces
        )

        return bool(missing_nonces)


if __name__ == "__main__":
    main(BindOperatorCharm)
