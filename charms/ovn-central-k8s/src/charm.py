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
    Set,
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
OVN_DB_CONTAINER_BY_DB = {"nb": OVN_NB_DB_CONTAINER, "sb": OVN_SB_DB_CONTAINER}
# Errors raised by OVN cluster operations, handled as best-effort failures.
CLUSTER_OP_ERRORS = (ops.pebble.Error, ValueError, tenacity.RetryError)


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
        # Set on upgrade-charm only: fresh units (e.g. replacements after a
        # scale-down) must never be backfilled, as their persistent storage
        # may hold stale databases from a previous unit.
        self._state.set_default(cluster_member_backfill_pending=False)
        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(
            self.on["peers"].relation_departed, self._on_peer_departed
        )
        self.framework.observe(
            self.on.leader_elected, self._on_reconcile_membership
        )
        self.framework.observe(
            self.on.update_status, self._on_reconcile_membership
        )

    def _on_upgrade_charm(self, event: ops.framework.EventBase):
        """Handle the upgrade charm event."""
        logger.info("Handling upgrade-charm event")
        self._state.cluster_member_backfill_pending = True
        if not self._backfill_cluster_member_flag():
            # Membership could not be determined yet (e.g. pebble is not
            # reachable in a container yet). Retry on the next hook:
            # without the flag the cluster join scripts would treat
            # this unit's databases as stale and wipe them.
            event.defer()
            return
        self.certs.validate_and_regenerate_certificates_if_needed()

    def _backfill_cluster_member_flag(self) -> bool:
        """Mark units with existing OVN databases as cluster members.

        Charm revisions predating the cluster_member flag have no
        marker, but existing members keep their databases on the
        persistent "databases" storage (/var/lib/ovn). The DB files
        hold the unit's RAFT server ID, so their presence means this
        unit already joined the clusters and the files must be kept.

        This only applies to units upgraded from such a revision. Fresh
        units, such as replacements after a scale-down, may find stale
        databases left on the storage by a previous unit; those must not
        be taken as proof of membership, so the join scripts can wipe
        them and join the clusters again.

        :returns: True if membership could be determined (no backfill
            pending, the flag was set, or the unit has no databases to
            protect), False if a container is not reachable yet and the
            check must be retried.
        :rtype: bool
        """
        if not self._state.cluster_member_backfill_pending:
            return True
        if self.peers.get_local_unit_value("cluster_member") == "true":
            self._state.cluster_member_backfill_pending = False
            return True

        db_files = {
            OVN_NB_DB_CONTAINER: "/var/lib/ovn/ovnnb_db.db",
            OVN_SB_DB_CONTAINER: "/var/lib/ovn/ovnsb_db.db",
        }
        for container_name, db_file in db_files.items():
            container = self.unit.get_container(container_name)
            if not container.can_connect():
                return False
            if not container.exists(db_file):
                # No databases on the persistent storage: this unit
                # never joined the clusters, nothing to backfill.
                self._state.cluster_member_backfill_pending = False
                return True

        logger.info("Backfilling OVN cluster member flag")
        self.peers.set_unit_data({"cluster_member": "true"})
        self._state.cluster_member_backfill_pending = False
        return True

    def _on_peer_departed(
        self, event: ops.charm.RelationDepartedEvent
    ) -> None:
        """Handle OVN cluster downscale when a peer is removed.

        Juju does not remove a scaled-away unit's server from the OVN RAFT
        clusters, so stale members accumulate and quorum is eventually lost.
        The departing unit gracefully leaves the clusters (cluster/leave)
        while its pod is still alive; a remaining leader waits for that to
        happen and kicks the server out as a fallback if it does not.
        """
        departing_unit = event.departing_unit
        if departing_unit is None:
            return

        if departing_unit.name == self.unit.name:
            # We are being removed: gracefully leave the OVN clusters.
            for db in OVN_DB_CONTAINER_BY_DB:
                try:
                    self.cluster_leave(db)
                except CLUSTER_OP_ERRORS as e:
                    logger.warning(
                        "Failed to leave the OVN %s cluster: %s", db, e
                    )
            return

        # We remain in the cluster; only the leader reconciles membership.
        if not self.unit.is_leader():
            return
        # The databag is unavailable once the unit is gone (e.g. on hook
        # retries), so fall back to the stable pod hostname derived from the
        # unit name, e.g. ovn-central/1 -> ovn-central-1.
        departing_data = event.relation.data.get(departing_unit)
        hostname = (
            departing_data.get("bound-hostname") if departing_data else None
        ) or departing_unit.name.replace("/", "-")
        self._remove_departing_server(hostname)

    def _remove_departing_server(self, hostname: str) -> None:
        """Remove a departed unit's server from the OVN RAFT clusters.

        :param hostname: Cluster hostname of the departed server
        :type hostname: str
        """
        for db in OVN_DB_CONTAINER_BY_DB:
            try:
                if self._wait_for_server_leave(db, hostname):
                    continue
                logger.warning(
                    "Departing unit %s did not leave the OVN %s cluster, "
                    "kicking it out",
                    hostname,
                    db,
                )
                self.cluster_kick(db, hostname)
            except CLUSTER_OP_ERRORS as e:
                logger.warning(
                    "Failed to remove %s from the OVN %s cluster: %s",
                    hostname,
                    db,
                    e,
                )

    def _on_reconcile_membership(self, event: ops.framework.EventBase) -> None:
        """Kick OVN RAFT servers that no longer map to a peer unit.

        The peer-departed handler is the fast path for downscale, but a
        removal can be missed: the departing unit may have been the leader
        (no surviving unit is leader yet when relation-departed fires), or
        the fallback kick may have failed transiently. Reconciling when
        leadership is (re)gained and periodically on update-status closes
        both gaps.

        Only servers listed in the live cluster status that no current peer
        unit claims are kicked; if the expected membership or the cluster
        status cannot be determined, nothing is removed.
        """
        if not self.unit.is_leader():
            return
        expected = self._expected_cluster_hostnames()
        if not expected:
            return

        def _is_stale(address: str) -> bool:
            return not any(
                self._hostname_matches_address(hostname, address)
                for hostname in expected
            )

        for db in OVN_DB_CONTAINER_BY_DB:
            try:
                self._kick_servers(db, _is_stale)
            except CLUSTER_OP_ERRORS as e:
                logger.warning(
                    "Failed to reconcile OVN %s cluster membership: %s",
                    db,
                    e,
                )

    def _expected_cluster_hostnames(self) -> Set[str]:
        """Cluster hostnames expected to be OVN RAFT members.

        Hostnames come from the bound-hostname each unit published, or are
        derived from the unit name, e.g. ovn-central/1 -> ovn-central-1.

        :returns: Hostnames of this unit and all current peer units, or an
            empty set if the peer relation is missing or a scale-up is in
            progress (a new unit can join the clusters before the leader
            sees it on the peer relation), so callers never kick blind.
        :rtype: Set[str]
        """
        relation = self.model.get_relation("peers")
        if relation is None:
            return set()
        hostnames = {
            relation.data[unit].get("bound-hostname")
            or unit.name.replace("/", "-")
            for unit in relation.units | {self.unit}
        }
        planned_units = self.app.planned_units()
        if len(hostnames) < planned_units:
            logger.debug(
                "Skipping OVN cluster membership reconcile: %d of %d "
                "planned units visible on the peer relation",
                len(hostnames),
                planned_units,
            )
            return set()
        return hostnames

    def _db_target_executor(self, db: str):
        """OVN appctl target and pebble executor for a database.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        """
        container = OVN_DB_CONTAINER_BY_DB[db]
        return "ovn{}_db".format(db), self.get_pebble_executor(container)

    def _kick_servers(self, db: str, should_kick) -> None:
        """Kick servers whose address matches a predicate from a cluster.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param should_kick: Called with each server address; servers for
            which it returns True are kicked.
        :type should_kick: Callable[[str], bool]
        """
        target, executor = self._db_target_executor(db)
        status = self.cluster_status(target, cmd_executor=executor)
        if not status:
            logger.warning(
                "Unable to get %s cluster status, skipping kick", target
            )
            return
        for server_id, address in status.servers:
            if not should_kick(address):
                continue
            logger.warning(
                "Kicking server %s (%s) from the OVN %s cluster",
                server_id,
                address,
                status.name,
            )
            ovn.ovn_appctl(
                target,
                ("cluster/kick", status.name, server_id),
                rundir=self.ovn_rundir(),
                cmd_executor=executor,
            )

    def cluster_kick(self, db: str, hostname: str) -> None:
        """Remove servers matching hostname from an OVN RAFT cluster.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param hostname: Cluster hostname of the server to remove
        :type hostname: str
        """
        self._kick_servers(
            db,
            lambda address: self._hostname_matches_address(hostname, address),
        )

    def cluster_leave(self, db: str) -> None:
        """Best-effort, non-blocking leave of an OVN RAFT cluster.

        The departing unit issues cluster/leave and returns immediately: its
        pod is being torn down, so blocking here (e.g. waiting for the leave
        to complete) risks the agent being killed mid-hook. The surviving
        leader reconciles membership and kicks the server as a fallback.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        """
        if not self.unit.get_container(
            OVN_DB_CONTAINER_BY_DB[db]
        ).can_connect():
            return
        target, executor = self._db_target_executor(db)
        status = self.cluster_status(target, cmd_executor=executor)
        if not status:
            return
        # A single-member cluster has nothing to leave.
        if len(status.servers) <= 1:
            return
        schema = status.name
        logger.info("Leaving the OVN %s cluster", schema)
        ovn.ovn_appctl(
            target,
            ("cluster/leave", schema),
            rundir=self.ovn_rundir(),
            cmd_executor=executor,
        )

    def _wait_for_server_leave(self, db: str, hostname: str) -> bool:
        """Wait for a departing server to remove itself from the cluster.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param hostname: Cluster hostname of the departing server
        :type hostname: str
        :returns: True if the server left, False on timeout.
        :rtype: bool
        """
        target, executor = self._db_target_executor(db)

        def _left() -> bool:
            # cluster_status raises RetryError once its internal retries
            # are exhausted, or pebble errors on immediate failures; keep
            # polling until the timeout instead of aborting the wait (and
            # silently skipping the fallback kick) on transient errors.
            try:
                status = self.cluster_status(target, cmd_executor=executor)
            except (ops.pebble.Error, tenacity.RetryError) as e:
                logger.debug(
                    "Unable to get %s cluster status while waiting for "
                    "%s to leave: %s",
                    target,
                    hostname,
                    e,
                )
                return False
            if status is None:
                return False
            return not any(
                self._hostname_matches_address(hostname, address)
                for _, address in status.servers
            )

        return self._poll_until(_left)

    @staticmethod
    def _hostname_matches_address(hostname: str, address: str) -> bool:
        """Whether an OVN RAFT server address belongs to a hostname.

        Server addresses have the form 'proto:host:port', e.g.
        'ssl:ovn-central-1:6643'. Compare host components rather than
        substrings so 'ovn-central-1' does not match 'ovn-central-10'.
        Only the first host label is compared, as the hostname may be
        an FQDN (socket.getfqdn()) or the short pod name derived from
        the unit name.

        :param hostname: Cluster hostname of the server
        :type hostname: str
        :param address: Server address from the cluster status
        :type address: str
        :returns: True if the address belongs to the hostname.
        :rtype: bool
        """
        parts = address.split(":")
        if len(parts) != 3:
            return False
        return parts[1].split(".")[0] == hostname.split(".")[0]

    @staticmethod
    def _poll_until(predicate) -> bool:
        """Poll a predicate until it is true, up to a bounded timeout.

        :returns: True if the predicate became true, False on timeout.
        :rtype: bool
        """
        try:
            return tenacity.Retrying(
                stop=tenacity.stop_after_delay(60),
                wait=tenacity.wait_fixed(3),
                retry=tenacity.retry_if_result(lambda done: not done),
            )(predicate)
        except tenacity.RetryError:
            return False

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
        self.peers.set_unit_data({"cluster_member": "true"})

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
        # Mark the unit as a cluster member as soon as the joins have
        # succeeded: on a hook retry the join scripts would otherwise
        # treat the freshly-joined DB files as stale and re-join.
        self.peers.set_unit_data({"cluster_member": "true"})
        logging.debug("Starting services in DB containers")
        for ph in self.get_named_pebble_handlers(OVN_DB_CONTAINERS):
            ph.start_service()
        # Attempt to setup listers etc
        self.configure_ovn()
        self.start_northd()
        self.check_pebble_handlers_ready()

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        # Retry a pending upgrade-time membership backfill until it
        # succeeds. It must complete before the container configs are
        # rendered below: the cluster join scripts wipe the local
        # databases of units wrongly considered non-members.
        if not self._backfill_cluster_member_flag():
            raise sunbeam_guard.WaitingExceptionError(
                "Waiting to determine OVN cluster membership"
            )
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
