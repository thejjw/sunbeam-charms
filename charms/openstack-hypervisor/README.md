# openstack-hypervisor

This charm deploys the openstack-hypervisor snap.

## CPU pinning profiles

When the `epa-orchestrator` snap is present on the hypervisor, the `openstack-hypervisor`
snap retrieves an EPA allocation of isolated CPU cores and configures Nova CPU pinning.

Use the charm config `cpu_topology_set` to define a CPU topology profile and tune how the
EPA allocated cores are split between Nova's `cpu_dedicated_set` and `cpu_shared_set`.

- **Default** (charm metadata): `dedicated_percentage` 10 and `requested_cores_percentage` 90. Invalid JSON (including an empty value) falls back to legacy behaviour: the snap uses EPA CPU sets as returned without this profile.
- **What the profile means**: `dedicated_percentage` (0-100) of the EPA allocated
  core set is used for `cpu_dedicated_set`. The remainder is used for `cpu_shared_set`.
- **Note**: `requested_cores_percentage` sizes the EPA allocation using the
  `allocate_cores_percent` socket action. The EPA `shared_cpus` set is ignored
  when a profile is applied.

Example:

```
juju config openstack-hypervisor cpu_topology_set='{
  "nova_profile": {
    "dedicated_percentage": 60,
    "requested_cores_percentage": 90
  }
}'
```

It is expected to be related to the control plane via cross model relations. To
achieve this assuming the control plane is in a model called *k8s*.

```
juju offer k8s.rabbitmq-k8s:amqp
juju offer k8s.keystone:identity-credentials
juju offer k8s.certificate-authority:certificates
juju offer k8s.ovn-relay:ovsdb-cms-relay

juju relate -m hypervisor openstack-hypervisor admin/k8s.rabbitmq-k8s
juju relate -m hypervisor openstack-hypervisor admin/k8s.keystone
juju relate -m hypervisor openstack-hypervisor admin/k8s.certificate-authority
juju relate -m hypervisor openstack-hypervisor admin/k8s.ovn-relay
```