#!/usr/bin/env python3

# Copyright 2022 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and

"""OVN Central Operator Charm.

This charm provide Glance services as part of an OpenStack deployment
"""

import logging
from typing import (
    List,
    Mapping,
)

import charms.ovn_central_k8s.v0.ovsdb as ovsdb
import ops
import ops.charm
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_ctxts
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.ovn.config_contexts as ovn_ctxts
import ops_sunbeam.ovn.container_handlers as ovn_chandlers
import ops_sunbeam.ovn.relation_handlers as ovn_rhandlers
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.tracing as sunbeam_tracing
import ovn
import ovsdb as ch_ovsdb
import tenacity
from ops.framework import (
    StoredState,
)

logger = logging.getLogger(__name__)

OVN_SB_DB_CONTAINER = "ovn-sb-db-server"
OVN_NB_DB_CONTAINER = "ovn-nb-db-server"
OVN_NORTHD_CONTAINER = "ovn-northd"
OVN_DB_CONTAINERS = [OVN_SB_DB_CONTAINER, OVN_NB_DB_CONTAINER]


@sunbeam_tracing.trace_type
class OVNNorthBPebbleHandler(ovn_chandlers.OVNPebbleHandler):
    """Handler for North OVN DB."""

    @property
    def wrapper_script(self):
        """Wrapper script for managing OVN service."""
        return "/root/ovn-northd-wrapper.sh"

    @property
    def status_command(self):
        """Status command for container."""
        return "/usr/share/ovn/scripts/ovn-ctl status_northd"

    @property
    def service_description(self):
        """Description of service."""
        return "OVN Northd"

    def default_container_configs(self):
        """Config files for container."""
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                "/etc/ovn/ovn-northd-db-params.conf", "root", "root"
            )
        )
        return _cc


@sunbeam_tracing.trace_type
class OVNNorthBDBPebbleHandler(ovn_chandlers.OVNPebbleHandler):
    """Handler for North-bound OVN DB."""

    @property
    def wrapper_script(self):
        """Wrapper script for managing OVN service."""
        return "/root/ovn-nb-db-server-wrapper.sh"

    @property
    def status_command(self):
        """Status command for container."""
        # This command always return 0 even if the DB service
        # is not running, so adding healthcheck with tcp check
        return "/usr/share/ovn/scripts/ovn-ctl status_ovsdb"

    @property
    def service_description(self):
        """Description of service."""
        return "OVN North Bound DB"

    def default_container_configs(self):
        """Config files for container."""
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                "/root/ovn-nb-cluster-join.sh", "root", "root"
            )
        )
        return _cc

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for OVN NB DB
        :rtype: dict
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "tcp": {"port": 6641},
                },
            }
        }


@sunbeam_tracing.trace_type
class OVNSouthBDBPebbleHandler(ovn_chandlers.OVNPebbleHandler):
    """Handler for South-bound OVN DB."""

    @property
    def wrapper_script(self):
        """Wrapper script for managing OVN service."""
        return "/root/ovn-sb-db-server-wrapper.sh"

    @property
    def status_command(self):
        """Status command for container."""
        # This command always return 0 even if the DB service
        # is not running, so adding healthcheck with tcp check
        return "/usr/share/ovn/scripts/ovn-ctl status_ovsdb"

    @property
    def service_description(self):
        """Description of service."""
        return "OVN South Bound DB"

    def default_container_configs(self):
        """Config files for container."""
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                "/root/ovn-sb-cluster-join.sh", "root", "root"
            )
        )
        return _cc

    def get_healthcheck_layer(self) -> dict:
        """Health check pebble layer.

        :returns: pebble health check layer configuration for OVN SB DB
        :rtype: dict
        """
        return {
            "checks": {
                "online": {
                    "override": "replace",
                    "level": "ready",
                    "tcp": {"port": 6642},
                },
            }
        }


@sunbeam_tracing.trace_sunbeam_charm
class OVNCentralOperatorCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Charm the service."""

    _state = StoredState()
    mandatory_relations = {"peers"}

    def __init__(self, framework: ops.framework.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)

    def _on_upgrade_charm(self, event: ops.framework.EventBase):
        """Handle the upgrade charm event."""
        logger.info("Handling upgrade-charm event")
        self.certs.validate_and_regenerate_certificates_if_needed()

    def get_pebble_handlers(self):
        """Pebble handlers for all OVN containers."""
        pebble_handlers = [
            OVNNorthBPebbleHandler(
                self,
                OVN_NORTHD_CONTAINER,
                "ovn-northd",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
            OVNSouthBDBPebbleHandler(
                self,
                OVN_SB_DB_CONTAINER,
                "ovn-sb-db-server",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
            OVNNorthBDBPebbleHandler(
                self,
                OVN_NB_DB_CONTAINER,
                "ovn-nb-db-server",
                self.container_configs,
                self.template_dir,
                self.configure_charm,
            ),
        ]
        return pebble_handlers

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler("peers", handlers):
            self.peers = ovn_rhandlers.OVNDBClusterPeerHandler(
                self,
                "peers",
                self.configure_charm,
                "peers" in self.mandatory_relations,
            )
            handlers.append(self.peers)
        if self.can_add_handler("ovsdb-cms", handlers):
            self.ovsdb_cms = ovn_rhandlers.OVSDBCMSProvidesHandler(
                self,
                "ovsdb-cms",
                self.configure_charm,
                mandatory="ovsdb-cms" in self.mandatory_relations,
            )
            handlers.append(self.ovsdb_cms)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(ovn_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts

    @property
    def databases(self) -> Mapping[str, str]:
        """Databases needed to support this charm.

        Return empty dict as no mysql databases are
        required.
        """
        return {}

    def ovn_rundir(self):
        """OVN run dir."""
        return "/var/run/ovn"

    def get_pebble_executor(self, container_name):
        """Execute command in pebble."""
        container = self.unit.get_container(container_name)

        def _run_via_pebble(*args):
            process = container.exec(list(args), timeout=5 * 60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("CMD Out: %s", line.strip())
            return out

        return _run_via_pebble

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        retry=tenacity.retry_if_exception_type(ops.pebble.ExecError),
        after=tenacity.after_log(logger, logging.WARNING),
        wait=tenacity.wait_exponential(multiplier=1, min=5, max=30),
    )
    def cluster_status(self, db, cmd_executor):
        """OVN version agnostic cluster_status helper.

        :param db: Database to operate on
        :type db: str
        :returns: Object describing the cluster status or None
        :rtype: Optional[ch_ovn.OVNClusterStatus]
        """
        try:
            # The charm will attempt to retrieve cluster status before OVN
            # is clustered and while units are paused, so we need to handle
            # errors from this call gracefully.
            return ovn.cluster_status(
                db, rundir=self.ovn_rundir(), cmd_executor=cmd_executor
            )
        except ValueError as e:
            logging.error(
                "Unable to get cluster status, ovsdb-server "
                "not ready yet?: {}".format(e)
            )
            return

    def configure_ovn_listener(self, db, port_map):
        """Create or update OVN listener configuration.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param port_map: Dictionary with port number and associated settings
        :type port_map: Dict[int,Dict[str,str]]
        :raises: ValueError
        """
        if db == "nb":
            executor = self.get_pebble_executor(OVN_NB_DB_CONTAINER)
        elif db == "sb":
            executor = self.get_pebble_executor(OVN_SB_DB_CONTAINER)
        status = self.cluster_status(
            "ovn{}_db".format(db), cmd_executor=executor
        )
        if status and status.is_cluster_leader:
            logging.debug(
                "configure_ovn_listener is_cluster_leader {}".format(db)
            )
            connections = ch_ovsdb.SimpleOVSDB(
                "ovn-{}ctl".format(db), cmd_executor=executor
            ).connection
            for port, settings in port_map.items():
                logging.debug("port {} {}".format(port, settings))
                # discover and create any non-existing listeners first
                for connection in connections.find(
                    'target="pssl:{}"'.format(port)
                ):
                    logging.debug("Found port {}".format(port))
                    break
                else:
                    logging.debug("Create port {}".format(port))
                    executor(
                        "ovn-{}ctl".format(db),
                        "--",
                        "--id=@connection",
                        "create",
                        "connection",
                        'target="pssl:{}"'.format(port),
                        "--",
                        "add",
                        "{}_Global".format(db.upper()),
                        ".",
                        "connections",
                        "@connection",
                    )
                # set/update connection settings
                for connection in connections.find(
                    'target="pssl:{}"'.format(port)
                ):
                    for k, v in settings.items():
                        logging.debug(
                            "set {} {} {}".format(
                                str(connection["_uuid"]), k, v
                            )
                        )
                        connections.set(str(connection["_uuid"]), k, v)

    def check_leader_ready(self):
        """Check leader is ready and has supplied mandatory data."""
        if self.supports_peer_relation and not (
            self.unit.is_leader() or self.is_leader_ready()
        ):
            raise sunbeam_guard.WaitingExceptionError("Leader not ready")
            missing_leader_data = [
                k for k in ["nb_cid", "sb_cid"] if not self.leader_get(k)
            ]
            if missing_leader_data:
                logging.debug(f"missing {missing_leader_data} from leader")
                self.unit.status = ops.model.WaitingStatus(
                    "Waiting for data from leader"
                )
                raise sunbeam_guard.WaitingExceptionError(
                    "Missing data from leader"
                )

    def start_northd(self):
        """Start northd service."""
        ph = self.get_named_pebble_handler(OVN_NORTHD_CONTAINER)
        ph.start_service()

    def configure_app_leader(self, event):
        """Run global app setup.

        These are tasks that should only be run once per application and only
        the leader runs them.
        """
        # Start services in North/South containers on lead unit
        logging.debug("Starting services in DB containers")
        for ph in self.get_named_pebble_handlers(OVN_DB_CONTAINERS):
            ph.start_service()
        # Attempt to setup listers etc
        self.configure_ovn()
        nb_status = self.cluster_status(
            "ovnnb_db", self.get_pebble_executor(OVN_NB_DB_CONTAINER)
        )
        sb_status = self.cluster_status(
            "ovnsb_db", self.get_pebble_executor(OVN_SB_DB_CONTAINER)
        )
        logging.debug("Telling peers leader is ready and cluster ids")
        self.set_leader_ready()
        self.leader_set(
            {
                "nb_cid": str(nb_status.cluster_id),
                "sb_cid": str(sb_status.cluster_id),
            }
        )
        self.set_leader_ready()
        self.start_northd()
        self.check_pebble_handlers_ready()

    def configure_app_non_leader(self, event):
        """Configure non leader."""
        if not self.peers.expected_peers_available():
            raise sunbeam_guard.WaitingExceptionError(
                "Expected peer units not ready, deferring cluster join"
            )

        logging.debug("Attempting to join OVN_Northbound cluster")
        container = self.unit.get_container(OVN_NB_DB_CONTAINER)
        process = container.exec(
            ["bash", "/root/ovn-nb-cluster-join.sh"], timeout=5 * 60
        )
        out, warnings = process.wait_output()
        if warnings:
            for line in warnings.splitlines():
                logger.warning("CMD Out: %s", line.strip())

        logging.debug("Attempting to join OVN_Southbound cluster")
        container = self.unit.get_container(OVN_SB_DB_CONTAINER)
        process = container.exec(
            ["bash", "/root/ovn-sb-cluster-join.sh"], timeout=5 * 60
        )
        out, warnings = process.wait_output()
        if warnings:
            for line in warnings.splitlines():
                logger.warning("CMD Out: %s", line.strip())
        logging.debug("Starting services in DB containers")
        for ph in self.get_named_pebble_handlers(OVN_DB_CONTAINERS):
            ph.start_service()
        # Attempt to setup listers etc
        self.configure_ovn()
        self.start_northd()
        self.check_pebble_handlers_ready()

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.open_ports()
        self.init_container_services()
        # Do not check_pebble_handlers_ready as northd is started later.
        self._state.unit_bootstrapped = True

    def open_ports(self):
        """Register ports in underlying cloud."""
        self.unit.open_port("tcp", 6641)
        self.unit.open_port("tcp", 6642)

    def configure_ovn(self):
        """Configure ovn listener."""
        inactivity_probe = (
            int(self.config["ovsdb-server-inactivity-probe"]) * 1000
        )
        self.configure_ovn_listener(
            "nb",
            {
                self.ovsdb_cms.db_nb_port: {
                    "inactivity_probe": inactivity_probe,
                },
            },
        )
        self.configure_ovn_listener(
            "sb",
            {
                self.ovsdb_cms.db_sb_port: {
                    "inactivity_probe": inactivity_probe,
                },
            },
        )
        self.configure_ovn_listener(
            "sb",
            {
                self.ovsdb_cms.db_sb_admin_port: {
                    "inactivity_probe": inactivity_probe,
                },
            },
        )


if __name__ == "__main__":  # pragma: nocover
    ops.main(OVNCentralOperatorCharm)
