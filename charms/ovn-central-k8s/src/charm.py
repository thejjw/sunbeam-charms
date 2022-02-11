#!/usr/bin/env python3
"""OVN Central Operator Charm.

This charm provide Glance services as part of an OpenStack deployment
"""

import ovn
import ovsdb as ch_ovsdb
import logging
from typing import List

import ops.charm
from ops.framework import StoredState
from ops.main import main

import advanced_sunbeam_openstack.charm as sunbeam_charm
import advanced_sunbeam_openstack.core as sunbeam_core
import advanced_sunbeam_openstack.relation_handlers as sunbeam_rhandlers
import advanced_sunbeam_openstack.config_contexts as sunbeam_ctxts
import advanced_sunbeam_openstack.ovn.container_handlers as ovn_chandlers
import advanced_sunbeam_openstack.ovn.config_contexts as ovn_ctxts
import advanced_sunbeam_openstack.ovn.relation_handlers as ovn_rhandlers

import charms.sunbeam_ovn_central_operator.v0.ovsdb as ovsdb

from charms.observability_libs.v0.kubernetes_service_patch \
    import KubernetesServicePatch

logger = logging.getLogger(__name__)

OVN_SB_DB_CONTAINER = "ovn-sb-db-server"
OVN_NB_DB_CONTAINER = "ovn-nb-db-server"
OVN_NORTHD_CONTAINER = "ovn-northd"
OVN_DB_CONTAINERS = [OVN_SB_DB_CONTAINER, OVN_NB_DB_CONTAINER]


class OVNNorthBPebbleHandler(ovn_chandlers.OVNPebbleHandler):

    @property
    def wrapper_script(self):
        return '/root/ovn-northd-wrapper.sh'

    @property
    def service_description(self):
        return 'OVN Northd'

    def default_container_configs(self):
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                '/etc/ovn/ovn-northd-db-params.conf',
                'root',
                'root'))
        return _cc


class OVNNorthBDBPebbleHandler(ovn_chandlers.OVNPebbleHandler):

    @property
    def wrapper_script(self):
        return '/root/ovn-nb-db-server-wrapper.sh'

    @property
    def service_description(self):
        return 'OVN North Bound DB'

    def default_container_configs(self):
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                '/root/ovn-nb-cluster-join.sh',
                'root',
                'root'))
        return _cc


class OVNSouthBDBPebbleHandler(ovn_chandlers.OVNPebbleHandler):

    @property
    def wrapper_script(self):
        return '/root/ovn-sb-db-server-wrapper.sh'

    @property
    def service_description(self):
        return 'OVN South Bound DB'

    def default_container_configs(self):
        _cc = super().default_container_configs()
        _cc.append(
            sunbeam_core.ContainerConfigFile(
                '/root/ovn-sb-cluster-join.sh',
                'root',
                'root'))
        return _cc


class OVNCentralOperatorCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = StoredState()

    def __init__(self, framework):
        super().__init__(framework)
        self.service_patcher = KubernetesServicePatch(
            self,
            [
                ('northbound', 6643),
                ('southbound', 6644),
            ]
        )

    def get_pebble_handlers(self):
        pebble_handlers = [
            OVNNorthBPebbleHandler(
                self,
                OVN_NORTHD_CONTAINER,
                'ovn-northd',
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm),
            OVNSouthBDBPebbleHandler(
                self,
                OVN_SB_DB_CONTAINER,
                'ovn-sb-db-server',
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm),
            OVNNorthBDBPebbleHandler(
                self,
                OVN_NB_DB_CONTAINER,
                'ovn-nb-db-server',
                self.container_configs,
                self.template_dir,
                self.openstack_release,
                self.configure_charm)]
        return pebble_handlers

    def get_relation_handlers(self, handlers=None) -> List[
            sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler('peers', handlers):
            self.peers = ovn_rhandlers.OVNDBClusterPeerHandler(
                self,
                'peers',
                self.configure_charm)
            handlers.append(self.peers)
        if self.can_add_handler('ovsdb-cms', handlers):
            self.ovsdb_cms = ovn_rhandlers.OVSDBCMSProvidesHandler(
                self,
                'ovsdb-cms',
                self.configure_charm)
            handlers.append(self.ovsdb_cms)
        handlers = super().get_relation_handlers(handlers)
        return handlers

    @property
    def config_contexts(self) -> List[sunbeam_ctxts.ConfigContext]:
        """Configuration contexts for the operator."""
        contexts = super().config_contexts
        contexts.append(
            ovn_ctxts.OVNDBConfigContext(self, "ovs_db"))
        return contexts

    def ovn_rundir(self):
        return '/var/run/ovn'

    def get_pebble_executor(self, container_name):
        container = self.unit.get_container(
            container_name)

        def _run_via_pebble(*args):
            process = container.exec(list(args), timeout=5*60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning('CMD Out: %s', line.strip())
            return out

        return _run_via_pebble

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
                db,
                rundir=self.ovn_rundir(),
                cmd_executor=cmd_executor)
        except (ValueError) as e:
            logging.error('Unable to get cluster status, ovsdb-server '
                          'not ready yet?: {}'.format(e))
            return

    def configure_ovn_listener(self, db, port_map):
        """Create or update OVN listener configuration.

        :param db: Database to operate on, 'nb' or 'sb'
        :type db: str
        :param port_map: Dictionary with port number and associated settings
        :type port_map: Dict[int,Dict[str,str]]
        :raises: ValueError
        """
        if db == 'nb':
            executor = self.get_pebble_executor(OVN_NB_DB_CONTAINER)
        elif db == 'sb':
            executor = self.get_pebble_executor(OVN_SB_DB_CONTAINER)
        status = self.cluster_status(
            'ovn{}_db'.format(db),
            cmd_executor=executor)
        if status and status.is_cluster_leader:
            logging.debug(
                'configure_ovn_listener is_cluster_leader {}'.format(db))
            connections = ch_ovsdb.SimpleOVSDB(
                'ovn-{}ctl'.format(db),
                cmd_executor=executor).connection
            for port, settings in port_map.items():
                logging.debug('port {} {}'.format(port, settings))
                # discover and create any non-existing listeners first
                for connection in connections.find(
                        'target="pssl:{}"'.format(port)):
                    logging.debug('Found port {}'.format(port))
                    break
                else:
                    logging.debug('Create port {}'.format(port))
                    executor(
                        'ovn-{}ctl'.format(db),
                        '--',
                        '--id=@connection',
                        'create', 'connection',
                        'target="pssl:{}"'.format(port),
                        '--',
                        'add', '{}_Global'.format(db.upper()),
                        '.', 'connections', '@connection')
                # set/update connection settings
                for connection in connections.find(
                        'target="pssl:{}"'.format(port)):
                    for k, v in settings.items():
                        logging.debug(
                            'set {} {} {}'
                            .format(str(connection['_uuid']), k, v))
                        connections.set(str(connection['_uuid']), k, v)

    def get_named_pebble_handlers(self, container_names):
        # XXX Move to ASO
        return [
            h
            for h in self.pebble_handlers
            if h.container_name in container_names
        ]

    def configure_charm(self, event: ops.framework.EventBase) -> None:
        """Catchall handler to configure charm services.

        """
        if not self.unit.is_leader():
            if not self.is_leader_ready():
                self.unit.status = ops.model.WaitingStatus(
                    "Waiting for leader to be ready")
                return
            missing_leader_data = [
                k for k in ['nb_cid', 'sb_cid']
                if not self.leader_get(k)]
            if missing_leader_data:
                logging.debug(f"missing {missing_leader_data} from leader")
                self.unit.status = ops.model.WaitingStatus(
                    "Waiting for data from leader")
                return
            logging.debug(
                "Remote leader is ready and has supplied all data needed")

        if not self.relation_handlers_ready():
            logging.debug("Aborting charm relations not ready")
            return

        # Render Config in all containers but init should *NOT* start
        # the service.
        for ph in self.pebble_handlers:
            if ph.pebble_ready:
                logging.debug(f"Running init for {ph.service_name}")
                ph.init_service(self.contexts())
            else:
                logging.debug(
                    f"Not running init for {ph.service_name},"
                    " container not ready")

        if self.unit.is_leader():
            # Start services in North/South containers on lead unit
            logging.debug("Starting services in DB containers")
            for ph in self.get_named_pebble_handlers(OVN_DB_CONTAINERS):
                ph.start_service()
            # Attempt to setup listers etc
            self.configure_ovn()
            nb_status = self.cluster_status(
                'ovnnb_db',
                self.get_pebble_executor(OVN_NB_DB_CONTAINER))
            sb_status = self.cluster_status(
                'ovnsb_db',
                self.get_pebble_executor(OVN_SB_DB_CONTAINER))
            logging.debug("Telling peers leader is ready and cluster ids")
            self.set_leader_ready()
            self.leader_set({
                'nb_cid': str(nb_status.cluster_id),
                'sb_cid': str(sb_status.cluster_id),
            })
            self.unit.status = ops.model.ActiveStatus()
        else:
            logging.debug("Attempting to join OVN_Northbound cluster")
            container = self.unit.get_container(OVN_NB_DB_CONTAINER)
            process = container.exec(
                ['bash', '/root/ovn-nb-cluster-join.sh'], timeout=5*60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning('CMD Out: %s', line.strip())

            logging.debug("Attempting to join OVN_Southbound cluster")
            container = self.unit.get_container(OVN_SB_DB_CONTAINER)
            process = container.exec(
                ['bash', '/root/ovn-sb-cluster-join.sh'], timeout=5*60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning('CMD Out: %s', line.strip())
            logging.debug("Starting services in DB containers")
            for ph in self.get_named_pebble_handlers(OVN_DB_CONTAINERS):
                ph.start_service()
            # Attempt to setup listers etc
            self.configure_ovn()
            self.unit.status = ops.model.ActiveStatus()

    def configure_ovn(self):
        inactivity_probe = int(
            self.config['ovsdb-server-inactivity-probe']) * 1000
        self.configure_ovn_listener(
            'nb', {
                self.ovsdb_cms.db_nb_port: {
                    'inactivity_probe': inactivity_probe,
                },
            })
        self.configure_ovn_listener(
            'sb', {
                self.ovsdb_cms.db_sb_port: {
                    'role': 'ovn-controller',
                    'inactivity_probe': inactivity_probe,
                },
            })
        self.configure_ovn_listener(
            'sb', {
                self.ovsdb_cms.db_sb_admin_port: {
                    'inactivity_probe': inactivity_probe,
                },
            })


class OVNCentralWallabyOperatorCharm(OVNCentralOperatorCharm):

    openstack_release = 'wallaby'

if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(OVNCentralWallabyOperatorCharm, use_juju_for_storage=True)
