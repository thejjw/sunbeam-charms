#!/usr/bin/env python3
#
# Copyright 2021 Canonical Ltd.
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

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""
import jinja2
import logging
from typing import (
    Callable,
    List,
    Mapping,
)

import ops.charm
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)

VALID_LOG_LEVELS = ["info", "debug", "warning", "error", "critical"]
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import charms.keystone_ldap_k8s.v0.domain_config as sunbeam_dc_svc
import ops_sunbeam.config_contexts as config_contexts
import json

class LDAPConfigFlagsContext(config_contexts.ConfigContext):
    """Configuration context for cinder parameters."""

    def context(self) -> dict:
        """Generate context information for cinder config."""
        config_flags = {}
        config = self.charm.model.config.get
        raw_config_flags = config("ldap-config-flags")
        if raw_config_flags:
            config_flags = json.loads(raw_config_flags)
        return {'flags': config_flags}


class DomainConfigProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity credentials relation."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for a domain config relation."""
        logger.debug("Setting up domain config event handler")
        self.domain_config = sunbeam_dc_svc.DomainConfigProvides(
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


class KeystoneLDAPK8SCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""
    DOMAIN_CONFIG_RELATION_NAME = "domain-config"

    def __init__(self, *args):
        super().__init__(*args)
        self.send_domain_config()

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
        contexts.append(LDAPConfigFlagsContext(self, "ldap_config_flags"))
        return contexts


    def send_domain_config(self, event=None):
       try:
            domain_name = self.config['domain-name']
        except KeyError:
            return
        loader = jinja2.FileSystemLoader(self.template_dir)
        _tmpl_env = jinja2.Environment(loader=loader)
        template = _tmpl_env.get_template("keystone.conf")
        self.dc_handler.domain_config.set_domain_info(
            domain_name=domain_name,
            config_contents=template.render(self.contexts()))

    def configure_app_leader(self, event):
        self.send_domain_config()
        self.set_leader_ready()

    @property
    def databases(self) -> Mapping[str, str]:
        return {}

    def get_pebble_handlers(self):
        return []

if __name__ == "__main__":  # pragma: nocover
    main(KeystoneLDAPK8SCharm)
