# openstack-hypervisor

This charm deploys the openstack-hypervisor snap.

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
