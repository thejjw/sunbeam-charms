=================
Reference Bundles
=================

The reference bundles in ``./bundles/`` deploy the Kubernetes-side
applications for the 2026.2 cycle. The bundles are sync to the ``2026.2/edge``
on Ubuntu 26.04. The required third-party charms use their own supported tracks
similar to the ones used in functional test CI's.

The bundles do not deploy the OVN control plane. OVN is provided by MicroOVN in
a machine model and integreated to the Kubernetes model using
``sunbeam-ovn-proxy``. See ``./deploy-sunbeam-charms.rst`` for the cross-model
``offer`` and ``integrate`` steps.

``minimal.yaml``
~~~~~~~~~~~~~~~~

The baseline barebones OpenStack: Keystone, Glance, Nova, Placement, Neutron,
MySQL, RabbitMQ, Traefik, and Vault.

``full.yaml``
~~~~~~~~~~~~~

The minimal bundle plus Horizon and Cinder.

Compute is not part of these Kubernetes bundles. To get the Compute
functionality, deploy ``openstack-hypervisor`` and ``openstack-network-agents``
in the machine model alongside MicroOVN.
