#!/usr/bin/env python3

#
# Copyright 2025 Canonical Ltd.
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

"""OpenStack Manila Data Operator Charm.

This charm provide manila-data capabilities for OpenStack.
This charm is responsible for managing the manila-data snap.
"""

import logging
from typing import (
    Mapping,
)

import ops
import ops_sunbeam.charm as charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as relation_handlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_sunbeam_charm
class ManilaDataOperatorCharm(charm.OSBaseOperatorCharmSnap):
    """OpenStack Manila Data Operator charm."""

    service_name = "manila-data"

    def __init__(self, framework):
        super().__init__(framework)
        self._state.set_default(api_ready=False)

    @property
    def snap_name(self) -> str:
        """Return snap name."""
        return str(self.model.config["snap-name"])

    @property
    def snap_channel(self) -> str:
        """Return snap channel."""
        return str(self.model.config["snap-channel"])

    def get_relation_handlers(
        self, handlers: list[relation_handlers.RelationHandler] | None = None
    ) -> list[relation_handlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = super().get_relation_handlers()
        return handlers

    def api_ready(self, event) -> None:
        """Event handler for bootstrap of service when api services are ready."""
        self._state.api_ready = True
        self.configure_charm(event)

    @property
    def databases(self) -> Mapping[str, str]:
        """Provide database name for manila services."""
        return {"database": "manila"}

    def configure_snap(self, event) -> None:
        """Run configuration on snap."""
        config = self.model.config.get
        try:
            contexts = self.contexts()
            snap_data = {
                "rabbitmq.url": contexts.amqp.transport_url,
                "database.url": contexts.database.connection,
                "settings.debug": config("debug"),
                "settings.enable-telemetry-notifications": config(
                    "enable-telemetry-notifications"
                ),
            }
        except AttributeError as e:
            raise sunbeam_guard.WaitingExceptionError(
                "Data missing: {}".format(e.name)
            )
        self.set_snap_data(snap_data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(ManilaDataOperatorCharm)
