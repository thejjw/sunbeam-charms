#!/usr/bin/env python3

import logging
import json
import sys
sys.path.append('lib') # noqa

import charmhelpers.contrib.storage.linux.ceph as ch_ceph
import charmhelpers.contrib.network.ip as ch_ip

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object
)

logger = logging.getLogger(__name__)


class BrokerAvailableEvent(EventBase):
    pass


class PoolAvailableEvent(EventBase):
    pass


class CephClientEvents(ObjectEvents):
    broker_available = EventSource(BrokerAvailableEvent)
    pools_available = EventSource(PoolAvailableEvent)


class CephClientRequires(Object):

    on = CephClientEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.name = relation_name
        self.this_unit = self.model.unit
        self.relation_name = relation_name
        self._stored.set_default(
            pools_available=False,
            broker_available=False,
            broker_req={})
        self.framework.observe(
            charm.on[relation_name].relation_joined,
            self.on_joined)
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self.on_changed)
        self.new_request = ch_ceph.CephBrokerRq()
        self.previous_requests = self.get_previous_requests_from_relations()

    def on_joined(self, event):
        relation = self.model.relations[self.relation_name]
        if relation:
            logging.info("emiting broker_available")
            self._stored.broker_available = True
            self.on.broker_available.emit()

    def request_osd_settings(self, settings):
        for relation in self.model.relations[self.relation_name]:
            relation.data[self.model.unit]['osd-settings'] = json.dumps(
                settings,
                sort_keys=True)

    @property
    def pools_available(self):
        return self._stored.pools_available

    @property
    def broker_available(self):
        return self._stored.broker_available

    def mon_hosts(self, mon_ips):
        """List of all monitor host public addresses"""
        hosts = []
        for ceph_addrs in mon_ips:
            # NOTE(jamespage): This looks odd but deals with
            #                  use with ceph-proxy which
            #                  presents all monitors in
            #                  a single space delimited field.
            for addr in ceph_addrs.split(' '):
                hosts.append(ch_ip.format_ipv6_addr(addr) or addr)
        hosts.sort()
        return hosts

    def get_relation_data(self):
        data = {}
        mon_ips = []
        for relation in self.model.relations[self.relation_name]:
            for unit in relation.units:
                _data = {
                    'key': relation.data[unit].get('key'),
                    'auth': relation.data[unit].get('auth')}
                mon_ip = relation.data[unit].get('ceph-public-address')
                if mon_ip:
                    mon_ips.append(mon_ip)
                if all(_data.values()):
                    data = _data
        if data:
            data['mon_hosts'] = self.mon_hosts(mon_ips)
        return data

    def existing_request_complete(self):
        rq = self.get_existing_request()
        if rq and self.is_request_complete(rq,
                                           self.model.relations[self.name]):
            return True
        return False

    def on_changed(self, event):
        logging.info("ceph client on_changed")
        relation_data = self.get_relation_data()
        if relation_data:
            if self.existing_request_complete():
                logging.info("emiting pools available")
                self._stored.pools_available = True
                self.on.pools_available.emit()
            else:
                logging.info("incomplete request. broker_req not found")

    def get_broker_rsp_key(self):
        return 'broker-rsp-{}'.format(self.this_unit.name.replace('/', '-'))

    def get_existing_request(self):
        logging.info("get_existing_request")
        # json.dumps of the CephBrokerRq()
        rq = ch_ceph.CephBrokerRq()

        if self._stored.broker_req:
            try:
                j = json.loads(self._stored.broker_req)
                logging.info(
                    "Json request: {}".format(self._stored.broker_req))
                rq.set_ops(j['ops'])
            except ValueError as err:
                logging.info(
                    "Unable to decode broker_req: %s. Error %s",
                    self._stored.broker_req,
                    err)
        return rq

    def _handle_broker_request(self, request_method, **kwargs):
        """Handle a broker request

        Add a ceph broker request using `request_method` and the provided
        `kwargs`.

        :param request_method: ch_ceph.CephBrokerRq method name to use for
                               request.
        :type request_method,: str
        """
        relations = self.model.relations[self.name]
        logging.info("%s: %s", request_method, relations)
        if not relations:
            return
        rq = self.new_request
        logging.info("Adding %s request", request_method)
        getattr(rq, request_method)(**kwargs)
        logging.info("Storing request")
        self._stored.broker_req = rq.request
        logging.info("Calling send_request_if_needed")
        self.send_request_if_needed(rq, relations)

    def _handle_pool_create_broker_request(self, request_method, **kwargs):
        """Process request to create a pool.

        :param request_method: ch_ceph.CephBrokerRq method name to use for
                               request.
        :type request_method: str
        :param app_name: Tag pool with application name. Note that there is
                         certain protocols emerging upstream with regard to
                         meaningful application names to use.
                         Examples are 'rbd' and 'rgw'.
        :type app_name: Optional[str]
        :param compression_algorithm: Compressor to use, one of:
                                      ('lz4', 'snappy', 'zlib', 'zstd')
        :type compression_algorithm: Optional[str]
        :param compression_mode: When to compress data, one of:
                                 ('none', 'passive', 'aggressive', 'force')
        :type compression_mode: Optional[str]
        :param compression_required_ratio: Minimum compression ratio for data
                                           chunk, if the requested ratio is not
                                           achieved the compressed version will
                                           be thrown away and the original
                                           stored.
        :type compression_required_ratio: Optional[float]
        :param compression_min_blob_size: Chunks smaller than this are never
                                          compressed (unit: bytes).
        :type compression_min_blob_size: Optional[int]
        :param compression_min_blob_size_hdd: Chunks smaller than this are not
                                              compressed when destined to
                                              rotational media (unit: bytes).
        :type compression_min_blob_size_hdd: Optional[int]
        :param compression_min_blob_size_ssd: Chunks smaller than this are not
                                              compressed when destined to flash
                                              media (unit: bytes).
        :type compression_min_blob_size_ssd: Optional[int]
        :param compression_max_blob_size: Chunks larger than this are broken
                                          into N * compression_max_blob_size
                                          chunks before being compressed
                                          (unit: bytes).
        :type compression_max_blob_size: Optional[int]
        :param compression_max_blob_size_hdd: Chunks larger than this are
                                              broken into
                                              N * compression_max_blob_size_hdd
                                              chunks before being compressed
                                              when destined for rotational
                                              media (unit: bytes)
        :type compression_max_blob_size_hdd: Optional[int]
        :param compression_max_blob_size_ssd: Chunks larger than this are
                                              broken into
                                              N * compression_max_blob_size_ssd
                                              chunks before being compressed
                                              when destined for flash media
                                              (unit: bytes).
        :type compression_max_blob_size_ssd: Optional[int]
        :param group: Group to add pool to
        :type group: Optional[str]
        :param max_bytes: Maximum bytes quota to apply
        :type max_bytes: Optional[int]
        :param max_objects: Maximum objects quota to apply
        :type max_objects: Optional[int]
        :param namespace: Group namespace
        :type namespace: Optional[str]
        :param weight: The percentage of data that is expected to be contained
                       in the pool from the total available space on the OSDs.
                       Used to calculate number of Placement Groups to create
                       for pool.
        :type weight: Optional[float]
        :raises: AssertionError
        """
        self._handle_broker_request(
            request_method,
            **kwargs)

    def create_replicated_pool(self, name, replicas=3, pg_num=None,
                               **kwargs):
        """Adds an operation to create a replicated pool.

        See docstring of `_handle_pool_create_broker_request` for additional
        common pool creation arguments.

        :param name: Name of pool to create
        :type name: str
        :param replicas: Number of copies Ceph should keep of your data.
        :type replicas: int
        :param pg_num: Request specific number of Placement Groups to create
                       for pool.
        :type pg_num: int
        :raises: AssertionError if provided data is of invalid type/range
        """
        self._handle_pool_create_broker_request(
            'add_op_create_replicated_pool',
            name=name,
            replica_count=replicas,
            pg_num=pg_num,
            **kwargs)

    def create_erasure_pool(self, name, erasure_profile=None,
                            allow_ec_overwrites=False, **kwargs):
        """Adds an operation to create a erasure coded pool.

        See docstring of `_handle_pool_create_broker_request` for additional
        common pool creation arguments.

        :param name: Name of pool to create
        :type name: str
        :param erasure_profile: Name of erasure code profile to use.  If not
                                set the ceph-mon unit handling the broker
                                request will set its default value.
        :type erasure_profile: str
        :param allow_ec_overwrites: allow EC pools to be overriden
        :type allow_ec_overwrites: bool
        :raises: AssertionError if provided data is of invalid type/range
        """
        self._handle_pool_create_broker_request(
            'add_op_create_erasure_pool',
            name=name,
            erasure_profile=erasure_profile,
            allow_ec_overwrites=allow_ec_overwrites,
            **kwargs)

    def create_erasure_profile(self, name,
                               erasure_type='jerasure',
                               erasure_technique=None,
                               k=None, m=None,
                               failure_domain=None,
                               lrc_locality=None,
                               shec_durability_estimator=None,
                               clay_helper_chunks=None,
                               device_class=None,
                               clay_scalar_mds=None,
                               lrc_crush_locality=None):
        """Adds an operation to create a erasure coding profile.

        :param name: Name of profile to create
        :type name: str
        :param erasure_type: Which of the erasure coding plugins should be used
        :type erasure_type: string
        :param erasure_technique: EC plugin technique to use
        :type erasure_technique: string
        :param k: Number of data chunks
        :type k: int
        :param m: Number of coding chunks
        :type m: int
        :param lrc_locality: Group the coding and data chunks into sets of size
                             locality (lrc plugin)
        :type lrc_locality: int
        :param durability_estimator: The number of parity chuncks each of which
                                     includes a data chunk in its calculation
                                     range (shec plugin)
        :type durability_estimator: int
        :param helper_chunks: The number of helper chunks to use for recovery
                              operations (clay plugin)
        :type: helper_chunks: int
        :param failure_domain: Type of failure domain from Ceph bucket types
                               to be used
        :type failure_domain: string
        :param device_class: Device class to use for profile (ssd, hdd)
        :type device_class: string
        :param clay_scalar_mds: Plugin to use for CLAY layered construction
                                (jerasure|isa|shec)
        :type clay_scaler_mds: string
        :param lrc_crush_locality: Type of crush bucket in which set of chunks
                                   defined by lrc_locality will be stored.
        :type lrc_crush_locality: string
        """
        self._handle_broker_request(
            'add_op_create_erasure_profile',
            name=name,
            erasure_type=erasure_type,
            erasure_technique=erasure_technique,
            k=k, m=m,
            failure_domain=failure_domain,
            lrc_locality=lrc_locality,
            shec_durability_estimator=shec_durability_estimator,
            clay_helper_chunks=clay_helper_chunks,
            device_class=device_class,
            clay_scalar_mds=clay_scalar_mds,
            lrc_crush_locality=lrc_crush_locality
        )

    def request_ceph_permissions(self, client_name, permissions):
        logging.info("request_ceph_permissions")
        relations = self.model.relations[self.name]
        if not relations:
            return
        rq = self.new_request
        rq.add_op({'op': 'set-key-permissions',
                   'permissions': permissions,
                   'client': client_name})
        self._stored.broker_req = rq.request
        # ch_ceph.send_request_if_needed(rq, relation=self.name)
        self.send_request_if_needed(rq, relations)

    def request_access_to_group(self,
                                name,
                                object_prefix_permissions,
                                permission):
        """Request access to a specific group of pools

        :param name: the group name to request access for
        :type name: string
        :param object_prefix_permissions: any hierarchy permissions neded
        :type object_prefix_permissions: dict
        :param permission: permissions for the specificed group of pools
        :type permission: string
        """
        logging.info("request_access_to_group")
        self._handle_broker_request(
            'add_op_request_access_to_group',
            name, object_prefix_permissions, permission
        )

    def get_previous_requests_from_relations(self):
        """Get the previous requests.

        :returns: The previous ceph requests.
        :rtype: Dict[str, ch_ceph.CephBrokerRq]
        """
        requests = {}
        for relation in self.model.relations[self.relation_name]:
            broker_req = relation.data[self.this_unit].get('broker_req')
            rid = "{}:{}".format(relation.name, relation.id)
            if broker_req:
                request_data = json.loads(broker_req)
                request = ch_ceph.CephBrokerRq(
                    api_version=request_data['api-version'],
                    request_id=request_data['request-id'])
                request.set_ops(request_data['ops'])
                requests[rid] = request
        return requests

    def get_request_states(self, request, relations):
        """Get the existing requests and their states.

        :param request: A CephBrokerRq object
        :type request: ch_ceph.CephBrokerRq
        :param relations: List of relations to check for existing request.
        :type relations: [ops.model.Relation, ...]
        :returns: Whether request is complete.
        :rtype: bool
        """
        complete = []
        requests = {}
        for relation in relations:
            rid = "{}:{}".format(relation.name, relation.id)
            complete = False
            previous_request = self.previous_requests.get(
                rid,
                ch_ceph.CephBrokerRq())
            if request == previous_request:
                sent = True
                complete = self.is_request_complete_for_relation(
                    previous_request,
                    relation)
            else:
                sent = False
                complete = False

            requests[rid] = {
                'sent': sent,
                'complete': complete,
            }

        return requests

    def is_request_complete_for_relation(self, request, relation):
        """Check if a given request has been completed on the given relation

        :param request: A CephBrokerRq object
        :type request: ch_ceph.CephBrokerRq
        :param relation: A relation to check for an existing request.
        :type relation: ops.model.Relation
        :returns: Whether request is complete.
        :rtype: bool
        """
        broker_key = self.get_broker_rsp_key()
        for unit in relation.units:
            if relation.data[unit].get(broker_key):
                rsp = ch_ceph.CephBrokerRsp(relation.data[unit][broker_key])
                if rsp.request_id == request.request_id:
                    if not rsp.exit_code:
                        return True
            else:
                if relation.data[unit].get('broker_rsp'):
                    logging.info('No response for this unit yet')
        return False

    def is_request_complete(self, request, relations):
        """Check a functionally equivalent request has already been completed

        Returns True if a similair request has been completed

        :param request: A CephBrokerRq object
        :type request: ch_ceph.CephBrokerRq
        :param relations: List of relations to check for existing request.
        :type relations: [ops.model.Relation, ...]
        :returns: Whether request is complete.
        :rtype: bool
        """
        states = self.get_request_states(request, relations)
        for rid in states.keys():
            if not states[rid]['complete']:
                return False

        return True

    def is_request_sent(self, request, relations):
        """Check if a functionally equivalent request has already been sent

        Returns True if a similair request has been sent

        :param request: A CephBrokerRq object
        :type request: ch_ceph.CephBrokerRq
        :param relations: List of relations to check for existing request.
        :type relations: [ops.model.Relation, ...]
        :returns: Whether equivalent request has been sent.
        :rtype: bool
        """
        states = self.get_request_states(request, relations)
        for rid in states.keys():
            if not states[rid]['sent']:
                return False

        return True

    def send_request_if_needed(self, request, relations):
        """Send request if an equivalent request has not already been sent

        :param request: A CephBrokerRq object
        :type request: ch_ceph.CephBrokerRq
        :param relations: List of relations to check for existing request.
        :type relations: [ops.model.Relation, ...]
        """
        states = self.get_request_states(request, relations)
        for relation in relations:
            rid = "{}:{}".format(relation.name, relation.id)
            if states[rid]['sent']:
                logging.debug(
                    ('Request %s is a duplicate of the previous broker request'
                     ' %s. Restoring previous broker request'),
                    request.request_id,
                    self.previous_requests[rid].request_id)
                # The previous request was stored at the beggining. The ops of
                # the new request match that of the old. But as the new request
                # was constructed broker data may have been set on the relation
                # during the construction of this request. This is because the
                # interface has no explicit commit method. Every op request has
                # in implicit send which updates the relation data. So make
                # sure # the relation data matches the data at the beggining so
                # that a new request is not triggered.
                request = self.previous_requests[rid]
            else:
                logging.debug('Sending request %s', request.request_id)
                relation.data[self.this_unit]['broker_req'] = request.request
