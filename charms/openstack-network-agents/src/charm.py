#!/usr/bin/env python3

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

"""Openstack Network Agents subordinate charm.

This charm deploys the `openstack-network-agents` snap and configures
OVS bridge mapping + optional chassis-as-gw on the network role node.
"""

from __future__ import (
    annotations,
)

import json
import logging
import subprocess
from dataclasses import (
    dataclass,
)
from urllib.parse import (
    quote,
    urlsplit,
)

import charms.operator_libs_linux.v2.snap as snap
import ndr_peering
import ops
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import tenacity
import yaml
from charms.loki_k8s.v1.loki_push_api import (
    LokiPushApiConsumer,
)
from cryptography import (
    x509,
)
from sunbeam_ndr_contract import CONTRACT_VERSION as NDR_CONTRACT_VERSION
from sunbeam_ndr_contract import (
    parse_instances,
)

logger = logging.getLogger(__name__)

MICROOVN_SNAP = "microovn"
MICROOVN_DAEMON_YAML = "/var/snap/microovn/common/state/daemon.yaml"
OVN_CHASSIS_PLUG = "ovn-chassis"
OVN_CHASSIS_SLOT = "microovn:ovn-chassis"
DATA_BINDING = "data"
NDR_CONFIG = "ndr.config"
NDR_LOGGING = "ndr.logging"


@dataclass(frozen=True)
class SavedNDRConfig:
    """Result of writing a unit's desired NDR config."""

    configured: bool
    changed: bool


def _placement_declarations(value: object) -> list[dict[str, object]]:
    """Validate placement and preserve service-name ordering."""
    return [
        item.declaration
        for item in sorted(
            parse_instances(value), key=lambda item: item.agent.service
        )
    ]


class AgentError(Exception):
    """Custom exception for Agent errors."""


@sunbeam_tracing.trace_type
class JujuInfoRequiresHandler(sunbeam_rhandlers.RelationHandler):
    """Relation handler for juju-info interface."""

    def setup_event_handler(self):
        """Set up event handler."""
        rel = self.charm.on[self.relation_name]
        self.framework.observe(rel.relation_joined, self._on_event)
        self.framework.observe(rel.relation_changed, self._on_event)
        self.framework.observe(rel.relation_broken, self._on_event)
        return None

    def _on_event(self, event: ops.RelationEvent) -> None:
        """Handle juju-info relation events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """True if at least one juju-info relation is present."""
        return bool(self.charm.model.relations.get(self.relation_name))


@sunbeam_tracing.trace_sunbeam_charm
class OpenstackNetworkAgentsOperatorCharm(
    sunbeam_charm.OSBaseOperatorCharmSnap
):
    """Snap-based subordinate for OVN provider networking and dynamic routing."""

    service_name = "openstack-network-agents"

    def __init__(self, framework: ops.framework.Framework) -> None:
        super().__init__(framework)
        self.framework.observe(
            self.on.update_status,
            self._on_update_status,
        )
        self.framework.observe(
            self.on.set_network_agents_local_settings_action,
            self._set_network_agents_local_settings_action,
        )
        self.framework.observe(
            self.on.list_nics_action,
            self._list_nics_action,
        )
        self.framework.observe(
            self.on.validate_dynamic_routing_instances_action,
            self._validate_dynamic_routing_instances_action,
        )
        self.framework.observe(
            self.on.set_dynamic_routing_instances_action,
            self._set_dynamic_routing_instances_action,
        )
        self.framework.observe(
            self.on.amqp_relation_broken, self._on_amqp_lost
        )
        self.framework.observe(
            self.on.amqp_relation_changed, self._on_amqp_changed
        )
        self._loki_consumer = LokiPushApiConsumer(
            self, relation_name="logging", forward_alert_rules=False
        )
        self.framework.observe(
            self.on.logging_relation_changed, self._on_logging_changed
        )
        self.framework.observe(
            self.on.logging_relation_broken, self._on_logging_changed
        )

    @property
    def data_address(self) -> str | None:
        """Get address from data binding."""
        use_binding = self.model.config.get("use-data-binding")
        if not use_binding:
            return None
        binding = self.model.get_binding(DATA_BINDING)
        if binding is None:
            return None
        address = binding.network.bind_address
        if address is None:
            return None
        return str(address)

    @property
    def snap_name(self) -> str:
        """Snap to install (configurable for dev/testing)."""
        return str(self.model.config.get("snap-name"))

    @property
    def snap_channel(self) -> str:
        """Channel to track in the Snap Store."""
        return str(self.model.config.get("snap-channel"))

    def _on_install(self, event: ops.InstallEvent):
        """Handle the install event."""
        try:
            super()._on_install(event)
        except sunbeam_guard.WaitingExceptionError as e:
            logger.warning("Deferring install event: %s", e.msg)
            event.defer()

    def _on_update_status(self, event: ops.UpdateStatusEvent):
        """Re-run configure_charm on update-status.

        The base class does not observe update-status.  As a subordinate
        whose principal (MicroOVN) may not be ready at initial deploy time,
        we need periodic retries to finish configuration once the snap
        installation succeeds via the deferred install event.
        """
        self.configure_charm(event)

    def _get_microovn_node_name(self) -> str | None:
        """Read the local MicroOVN node name from daemon.yaml."""
        try:
            with open(MICROOVN_DAEMON_YAML) as f:
                data = yaml.safe_load(f)
            return data.get("name")
        except (FileNotFoundError, PermissionError, yaml.YAMLError) as e:
            logger.warning("Failed to read %s: %s", MICROOVN_DAEMON_YAML, e)
            return None

    def _get_microovn_node_services(self) -> set[str]:
        """Get the MicroOVN cluster services for the local node.

        Reads the node name from daemon.yaml, then parses the output of
        ``microovn status`` to find the services assigned to this node
        (e.g. ``{"central", "chassis", "switch"}``).
        """
        node_name = self._get_microovn_node_name()
        if not node_name:
            return set()

        try:
            result = subprocess.run(
                ["microovn", "status"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(
                    "microovn status failed: %s", result.stderr.strip()
                )
                return set()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Failed to run microovn status: %s", e)
            return set()

        lines = result.stdout.splitlines()
        for i, line in enumerate(lines):
            if node_name in line:
                if i + 1 < len(lines):
                    svc_line = lines[i + 1].strip()
                    if svc_line.startswith("Services:"):
                        svc_str = svc_line.removeprefix("Services:")
                        return {s.strip() for s in svc_str.split(",")}
        return set()

    def _check_microovn_ready(self) -> bool:
        """Check if microovn snap is ready.

        The microovn snap must be present and the node must have the
        ``switch`` service listed in ``microovn status``.
        """
        microovn = self.snap_module.SnapCache()[MICROOVN_SNAP]
        if not microovn.present:
            logger.warning("%s snap is not present", MICROOVN_SNAP)
            return False

        node_services = self._get_microovn_node_services()
        if not node_services:
            logger.warning(
                "Could not determine %s services for this node",
                MICROOVN_SNAP,
            )
            return False

        if "switch" not in node_services:
            logger.warning(
                "%s node is missing the switch service", MICROOVN_SNAP
            )
            return False
        return True

    def ensure_snap_present(self):
        """Install snap after verifying microovn readiness."""
        if not self._check_microovn_ready():
            raise sunbeam_guard.WaitingExceptionError(
                "Microovn snap is not ready"
            )
        return super().ensure_snap_present()

    def ensure_services_running(self, enable: bool = True) -> None:
        """The network-agents snap supervises configured routing agents."""
        pass

    def stop_services(self, relation: set[str] | None = None) -> None:
        """Stop agent instances when the charm loses its principal or AMQP."""
        if (
            relation is None or relation.intersection({"juju-info", "amqp"})
        ) and (self.get_snap().present):
            self._configure_dynamic_routing(amqp_available=False)

    def get_relation_handlers(
        self, handlers: list[sunbeam_rhandlers.RelationHandler] | None = None
    ) -> list[sunbeam_rhandlers.RelationHandler]:
        """Return a list of relation handlers used by this charm."""
        handlers = handlers or []
        if self.can_add_handler("juju-info", handlers):
            juju_info = JujuInfoRequiresHandler(
                self,
                "juju-info",
                self.configure_charm,
                "juju-info" in self.mandatory_relations,
            )
            handlers.append(juju_info)
        return super().get_relation_handlers(handlers)

    def _connect_ovn_chassis(self) -> None:
        """Connect the snap ovn-chassis plug to microovn:ovn-chassis."""
        openstack_network_agents = self.get_snap()

        try:
            openstack_network_agents.connect(
                OVN_CHASSIS_PLUG, slot=OVN_CHASSIS_SLOT
            )
            logger.info(
                "Connected microovn:ovn-chassis slot to openstack-network-agents:ovn-chassis plug"
            )
        except snap.SnapError as e:
            logger.error(
                f"Failed to connect to microovn:ovn-chassis: {e.message}"
            )
            raise

    def _set_network_agents_local_settings_action(
        self, event: ops.ActionEvent
    ) -> None:
        """Action to set per-unit local settings for provider networking."""
        local_settings = [
            "network.external-interface",
            "network.bridge-name",
            "network.physnet-name",
            "network.bridge-mapping",
            "network.enable-chassis-as-gw",
        ]

        new_snap_settings = {}
        for setting in local_settings:
            action_param = setting.split(".")[1]
            # enable-chassis-as-gw is a boolean, so we want to allow false values to be set
            if event.params.get(action_param) is not None:
                new_snap_settings[setting] = event.params.get(action_param)
        if new_snap_settings:
            self.set_snap_data(new_snap_settings)

    def _dynamic_routing_transport(self) -> dict[str, str]:
        """Get transport settings for the same RabbitMQ vhost as Neutron."""
        interface = self.amqp.interface
        if not (hostname := interface.hostname) or not (
            password := interface.password
        ):
            return {}
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        ssl_port = interface.ssl_port
        ca_cert = interface.ssl_ca
        if bool(ssl_port) != bool(ca_cert):
            # Relation data can arrive in steps. Keep agents paused if
            # only one half of the advertised TLS configuration is present.
            return {}
        port = ssl_port or self.amqp.DEFAULT_PORT
        transport = {
            "transport-url": (
                f"rabbit://{quote(self.amqp.username, safe='')}:"
                f"{quote(password, safe='')}@{hostname}:{port}/"
                f"{quote(self.amqp.vhost, safe='')}"
            ),
        }
        if ca_cert:
            try:
                x509.load_pem_x509_certificates(ca_cert.encode())
            except ValueError:
                return {}
            transport["ca-cert"] = ca_cert
        return transport

    def _read_dynamic_routing_config(self) -> dict | None:
        raw = (
            self.get_snap().get(None, typed=True).get("ndr", {}).get("config")
        )
        return json.loads(raw) if isinstance(raw, str) else raw

    def _configure_dynamic_routing(
        self, *, amqp_available: bool = True
    ) -> None:
        """Update agent credentials for saved placement after relation changes."""
        saved = self._save_dynamic_routing_config(
            amqp_available=amqp_available
        )
        if saved.changed:
            self._run_dynamic_routing("reconcile")

    def _save_dynamic_routing_config(
        self,
        instances: list[dict[str, object]] | None = None,
        *,
        amqp_available: bool = True,
    ) -> SavedNDRConfig:
        """Save placement and broker credentials in snapd."""
        snap_service = self.get_snap()
        current = self._read_dynamic_routing_config()
        # Import older placements once if AMQP loss cleared the snap config.
        legacy = getattr(self._state, "ndr_instances", None)
        if instances is None:
            if current is not None:
                instances = current.get(
                    "paused-instances", current["instances"]
                )
            elif legacy is not None:
                instances = json.loads(legacy)
            else:
                return SavedNDRConfig(configured=False, changed=False)

        transport = (
            self._dynamic_routing_transport()
            if instances and amqp_available
            else {}
        )
        desired = {"instances": instances, **transport}
        if instances and not transport:
            # Older snaps stop when the runnable list is empty.
            desired = {"instances": [], "paused-instances": instances}
        changed = current != desired
        if changed:
            snap_service.set(
                {NDR_CONFIG: json.dumps(desired, sort_keys=True)}, typed=True
            )
        if legacy is not None:
            self._state.ndr_instances = None
        return SavedNDRConfig(
            configured=not instances or bool(transport), changed=changed
        )

    def _run_dynamic_routing(
        self, command: str, payload: str | None = None
    ) -> dict:
        """Call the installed snap's host placement interface."""
        result = subprocess.run(
            ["snap", "run", self.snap_name, "dynamic-routing", command],
            input=payload,
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode:
            if command == "validate" and result.stderr.strip():
                raise ValueError(result.stderr.strip())
            raise ValueError(
                f"Network agents snap failed to {command} dynamic routing."
            )
        response = json.loads(result.stdout)
        if not isinstance(response, dict):
            raise ValueError(
                "Network agents snap returned invalid dynamic routing data."
            )
        return response

    def _validate_dynamic_routing_plan(
        self, event: ops.ActionEvent
    ) -> tuple[list[dict[str, object]], dict[str, str]]:
        """Return the snap's canonical attachment plan and managed addresses."""
        if event.params["contract-version"] != NDR_CONTRACT_VERSION:
            raise ValueError(
                "Unsupported dynamic routing placement contract version."
            )
        instances = _placement_declarations(
            json.loads(event.params["instances"])
        )
        response = self._run_dynamic_routing("validate", json.dumps(instances))
        if response.get("contract-version") != NDR_CONTRACT_VERSION:
            raise ValueError(
                "Installed network agents snap has an incompatible NDR contract."
            )
        instances = _placement_declarations(response.get("instances"))
        addresses = ndr_peering.validate_addresses(
            response.get("managed-addresses")
        )
        ndr_peering.managed_fragment(
            ndr_peering.NETPLAN_PATH, required=bool(addresses)
        )
        return instances, addresses

    def _validate_dynamic_routing_instances_action(
        self, event: ops.ActionEvent
    ) -> None:
        """Validate the complete unit plan without modifying network or snap state."""
        try:
            instances, _ = self._validate_dynamic_routing_plan(event)
            current = self._read_dynamic_routing_config() or {}
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
        ) as error:
            event.fail(str(error))
            return
        event.set_results(
            {
                "contract-version": NDR_CONTRACT_VERSION,
                "instances": json.dumps(instances),
                "saved-instances": json.dumps(
                    current.get(
                        "paused-instances", current.get("instances", [])
                    )
                ),
            }
        )

    def _set_dynamic_routing_instances_action(
        self, event: ops.ActionEvent
    ) -> None:
        """Replace the desired dynamic routing plans for this unit."""
        try:
            instances, addresses = self._validate_dynamic_routing_plan(event)
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            ValueError,
        ) as error:
            event.fail(str(error))
            return
        try:
            saved = self._save_dynamic_routing_config(instances)
            self._run_dynamic_routing("reconcile")
            # The snap creates protected ports before Netplan assigns addresses.
            # On removal, the snap stops agents and removes ports first.
            ndr_peering.reconcile_addresses(
                ndr_peering.NETPLAN_PATH, addresses
            )
            ready = self._dynamic_routing_ready()
        except (
            self.snap_module.SnapError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
        ):
            event.fail(
                "Failed to reconcile dynamic routing on this unit. Correct the host configuration and retry."
            )
            return
        event.set_results(
            {
                "configured": saved.configured and ready,
                "instances": len(instances),
            }
        )

    @tenacity.retry(
        retry=tenacity.retry_if_result(lambda ready: not ready),
        stop=tenacity.stop_after_attempt(4),
        wait=tenacity.wait_fixed(1),
        retry_error_callback=lambda _: False,
    )
    def _dynamic_routing_ready(self) -> bool:
        """Allow a short delay for asynchronous address assignment and IPv6 DAD."""
        return self._run_dynamic_routing("reconcile").get("ready") is True

    def _on_amqp_lost(self, event: ops.RelationBrokenEvent) -> None:
        """Remove the transport immediately when the AMQP relation is removed."""
        self.stop_services({"amqp"})

    def _on_amqp_changed(self, event: ops.RelationChangedEvent) -> None:
        """Stop agents if credentials are removed without breaking the relation."""
        if not self.amqp.ready:
            self.stop_services({"amqp"})

    def _on_logging_changed(self, event: ops.RelationEvent) -> None:
        """Update log forwarding without touching the agent configuration."""
        self._configure_dynamic_routing_logging(
            available=not isinstance(event, ops.RelationBrokenEvent)
        )

    def _loki_endpoint_urls(self) -> list[str]:
        """Return deduplicated Loki endpoints that contain no credentials."""
        urls = set()
        for endpoint in self._loki_consumer.loki_endpoints:
            url = endpoint["url"]
            parts = urlsplit(url)
            if (
                parts.scheme in {"http", "https"}
                and parts.hostname
                and parts.username is None
                and parts.password is None
            ):
                urls.add(url)
        return sorted(urls)

    def _configure_dynamic_routing_logging(
        self, available: bool = True
    ) -> None:
        """Pass Loki endpoints and Juju topology to the snap separately."""
        snap_service = self.get_snap()
        if not snap_service.present:
            return
        try:
            endpoints = self._loki_endpoint_urls() if available else []
        except (AttributeError, TypeError, ValueError):
            logger.warning("Ignoring malformed Loki endpoint relation data.")
            return
        current = (
            snap_service.get(None, typed=True).get("ndr", {}).get("logging")
        )
        if not endpoints and current is None:
            return
        desired = {
            "endpoints": endpoints,
            "labels": {
                "juju_model": self.model.name,
                "juju_model_uuid": self.model.uuid,
                "juju_application": self.app.name,
                "juju_unit": self.unit.name,
            },
        }
        rendered = json.dumps(desired, sort_keys=True)
        if current not in (desired, rendered):
            try:
                snap_service.set({NDR_LOGGING: rendered}, typed=True)
            except self.snap_module.SnapError:
                # Logging is optional and must not block agent configuration.
                logger.warning(
                    "The snap could not configure NDR log forwarding."
                )

    def configure_snap(self, event: ops.EventBase) -> None:
        """Push configuration into the snap."""
        self._connect_ovn_chassis()
        snap_data = {
            "settings.debug": bool(self.model.config.get("debug") or False),
            "network.external-bridge-address": str(
                self.model.config.get("external-bridge-address")
            ),
        }
        data_addr = self.data_address
        if data_addr:
            snap_data["network.ip-address"] = data_addr
        self.set_snap_data(snap_data)
        self._configure_dynamic_routing()
        self._configure_dynamic_routing_logging()

    def _agent_cli_cmd(self, cmd: str):
        """Helper to run cli commands on the snap."""
        process = subprocess.run(
            [
                "snap",
                "run",
                self.snap_name,
                "--verbose",
            ]
            + cmd.split(),
            capture_output=True,
        )

        stderr = process.stderr.decode("utf-8")
        logger.debug("logs: %s", stderr)
        stdout = process.stdout.decode("utf-8")
        logger.debug("stdout: %s", stdout)
        if process.returncode != 0:
            raise AgentError(stderr)

        return stdout

    def _list_nics_action(self, event: ops.ActionEvent):
        """Run list_nics action."""
        try:
            stdout = self._agent_cli_cmd("list-nics --format json")
        except AgentError as e:
            event.fail(str(e))
            return

        # cli returns a json dict with keys "nics" and "candidate"
        event.set_results({"result": stdout})


if __name__ == "__main__":  # pragma: nocover
    ops.main(OpenstackNetworkAgentsOperatorCharm)
