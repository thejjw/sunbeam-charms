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

"""Keystone LDAP configuration.

Send domain configuration to the keystone charm.
"""

import json
import logging
from typing import (
    List,
    Mapping,
)

import charms.keystone_k8s.v0.domain_config as sunbeam_dc_svc
import jinja2
import ops
import ops.charm
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as config_contexts
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class LDAPConfigContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

    def context(self) -> dict:
        """Generate context information for cinder config."""
        config = {}
        raw_config_flags = self.charm.model.config["ldap-config-flags"]
        if raw_config_flags:
            try:
                config = json.loads(raw_config_flags)
            except json.decoder.JSONDecodeError:
                logger.error("JSON Error, cannot load config flags")
        return {"config": config}


@sunbeam_tracing.trace_type
class DomainConfigProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity credentials relation."""

    def setup_event_handler(self):
        """Configure event handlers for a domain config relation."""
        logger.debug("Setting up domain config event handler")
        self.domain_config = sunbeam_tracing.trace_type(
            sunbeam_dc_svc.DomainConfigProvides
        )(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            self.domain_config.on.remote_ready,
            self._on_domain_config_ready,
        )
        return self.domain_config

    def _on_domain_config_ready(self, event) -> None:
        """Handles domain config change events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check if handler is ready."""
        return True


@sunbeam_tracing.trace_sunbeam_charm
class KeystoneLDAPK8SCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    DOMAIN_CONFIG_RELATION_NAME = "domain-config"

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler(self.DOMAIN_CONFIG_RELATION_NAME, handlers):
            self.dc_handler = DomainConfigProvidesHandler(
                self,
                self.DOMAIN_CONFIG_RELATION_NAME,
                self.send_domain_config,
            )
            handlers.append(self.dc_handler)
        return super().get_relation_handlers(handlers)

    @property
    def config_contexts(self) -> List[config_contexts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(LDAPConfigContext(self, "ldap_config"))
        return contexts

    def send_domain_config(self, event=None) -> None:
        """Send domain configuration to keystone."""
        try:
            domain_name = self.config["domain-name"]
        except KeyError:
            return
        loader = jinja2.FileSystemLoader(self.template_dir)
        _tmpl_env = jinja2.Environment(loader=loader)
        template = _tmpl_env.get_template("keystone.conf")
        self.dc_handler.domain_config.set_domain_info(
            domain_name=domain_name,
            config_contents=template.render(self.contexts()),
            ca=self.config.get("tls-ca-ldap"),
        )

    def configure_app_leader(self, event) -> None:
        """Configure application."""
        self.send_domain_config()
        self.set_leader_ready()

    @property
    def databases(self) -> Mapping[str, str]:
        """Config charm has no databases."""
        return {}

    def get_pebble_handlers(self):
        """Config charm has no containers."""
        return []


if __name__ == "__main__":  # pragma: nocover
    ops.main(KeystoneLDAPK8SCharm)
