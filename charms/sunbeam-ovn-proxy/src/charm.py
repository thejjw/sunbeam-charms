#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Sunbeam OVN Proxy Charm.

This charm acts as a minimal relation proxy bridging the ovsdb relation
(from MicroOVN) to the ovsdb-cms relation (for Sunbeam charms) with direct
pass-through data flow.
"""

import logging

import charms.ovn_central_k8s.v0.ovsdb as ovsdb  # type: ignore[import-untyped]
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing

logger = logging.getLogger(__name__)


@sunbeam_tracing.trace_type
class OVSDBCMSProxyProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handle provides side of ovsdb-cms."""

    interface: ovsdb.OVSDBCMSProvides

    def setup_event_handler(self) -> ops.Object:
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up ovs-cms provides event handler")

        ovsdb_svc = sunbeam_tracing.trace_type(ovsdb.OVSDBCMSProvides)(
            self.charm,
            self.relation_name,
            proxy_relation=True,
        )
        self.framework.observe(ovsdb_svc.on.ready, self._on_callback)
        self.framework.observe(ovsdb_svc.on.goneaway, self._on_callback)
        return ovsdb_svc

    def _on_callback(self, event: ops.EventBase) -> None:
        """Handle OVSDB CMS change events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Whether the interface is ready."""
        return len(self.model.relations[self.relation_name]) > 0

    def clear(self) -> None:
        """Clear all relation data for all relations."""
        self.interface.clear_app_data()

    def provide(self, data: dict[str, str]) -> None:
        """Provide data to all relations."""
        self.interface.set_app_data(data)


@sunbeam_tracing.trace_sunbeam_charm
class SunbeamOvnProxyCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm that proxies OVSDB relations between MicroOVN and Sunbeam charms."""

    service_name = "sunbeam-ovn-proxy"

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Register relation handlers for the proxy charm."""
        handlers = handlers or []

        # Handler for the requires side (ovsdb from MicroOVN)
        if self.can_add_handler("ovsdb", handlers):
            self.ovsdb = ovn_rhandlers.MicroOVSDBRequiresHandler(
                self,
                "ovsdb",
                self.configure_charm,
                mandatory="ovsdb" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb)

        # Handler for the provides side (ovsdb-cms to Sunbeam charms)
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = OVSDBCMSProxyProvidesHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                mandatory="ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)

        return super().get_relation_handlers(handlers)

    def _update_ovsdb_cms_data(self) -> None:
        """Update ovsdb-cms relation with data from ovsdb relation."""
        if not self.ovsdb.ready:
            logger.debug("ovsdb relation not ready, skipping update")
            self._clear_ovsdb_cms_data()
            return
        if not self.ovsdb_cms.ready:
            logger.debug("ovsdb-cms relation not ready, skipping update")
            return

        ovsdb_context = self.ovsdb.context()

        logger.info("Propagating OVSDB data: %s", ovsdb_context)
        self.ovsdb_cms.provide(ovsdb_context)

    def _clear_ovsdb_cms_data(self) -> None:
        """Clear ovsdb-cms relation data when ovsdb relation is unavailable."""
        if not self.ovsdb_cms.ready:
            logger.debug("ovsdb-cms relation not ready, skipping clear")
            return

        logger.info("Clearing ovsdb-cms relation data (ovsdb unavailable)")
        self.ovsdb_cms.clear()

    def configure_charm(self, event: ops.EventBase) -> None:
        """Configure charm."""
        self._update_ovsdb_cms_data()
        return super().configure_charm(event)


if __name__ == "__main__":  # pragma: nocover
    ops.main(SunbeamOvnProxyCharm)
