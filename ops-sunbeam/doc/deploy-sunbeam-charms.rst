============================
How-To deploy sunbeam charms
============================

Sunbeam charms requires juju environment with a registered kubernetes cloud.

Below are the steps to deploy sunbeam charms on `juju with microk8s cloud`_
on a single node.

Install microk8s
~~~~~~~~~~~~~~~~

1. Install microk8s snap

   Run below commands to install microk8s snap

.. code-block:: bash

   sudo snap install microk8s --classic
   sudo usermod -a -G microk8s $USER
   sudo chown -f -R $USER ~/.kube
   su - $USER
   microk8s status --wait-ready

2. If required, set proxy variables

   Change the proxy values as per the environment.

.. code-block:: bash

   echo "HTTPS_PROXY=http://squid.internal:3128" >> /var/snap/microk8s/current/args/containerd-env
   echo "NO_PROXY=10.0.0.0/8,192.168.0.0/16,127.0.0.0/8,172.16.0.0/16" >> /var/snap/microk8s/current/args/containerd-env
   sudo systemctl restart snap.microk8s.daemon-containerd.service

3. Enable add-ons

   In the below commands, change the following
   * ``10.245.160.2`` to point to DNS server
   * ``10.5.100.100-10.5.100.110`` to IP range allocations for loadbalancers

.. code-block:: bash

   microk8s enable dns:10.245.160.2
   microk8s enable hostpath-storage
   microk8s enable metallb:10.5.100.100-10.5.100.110

Install juju
~~~~~~~~~~~~

Run below commands to install juju controller on microk8s

.. code-block:: bash

   sudo snap install juju --classic
   juju bootstrap --config controller-service-type=loadbalancer microk8s micro

Deploy Sunbeam charms
~~~~~~~~~~~~~~~~~~~~~

See ``./reference-bundles.rst`` for information about example bundles available
here. The example bundles target Ubuntu 26.04 and pin the charms maintained in
this repository to the ``2026.2/edge`` channel.

To use locally built charms, update the following in the bundle

* ``charm:`` to point to locally built charm file
* ``channel:`` should be commented

Run below commands to deploy the bundle

.. code-block:: bash

   juju add-model sunbeam
   juju deploy ./doc/bundles/full.yaml --trust

The bundles deploy the Kubernetes control plane only. Neutron's
``ovsdb-cms`` endpoint is provided by ``sunbeam-ovn-proxy``, a machine charm
that runs in the model hosting MicroOVN.

These steps assume MicroOVN is already deployed in a machine model. Integrate
the OVN proxy with MicroOVN, and offer its ``ovsdb-cms`` endpoint:

.. code-block:: bash

   juju switch <machine-model>
   juju deploy sunbeam-ovn-proxy --channel 2026.2/edge
   juju integrate microovn:ovsdb sunbeam-ovn-proxy:ovsdb
   juju offer sunbeam-ovn-proxy:ovsdb-cms

Integrate Neutron with the offer from the Kubernetes model:

.. code-block:: bash

   juju switch micro:sunbeam
   juju integrate neutron:ovsdb-cms <machine-controller>:admin/<machine-model>.sunbeam-ovn-proxy

Neutron stays blocked until this integration is made.

Check ``juju status`` and wait for all units to be active.

Testing OpenStack Control plane
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Install openstackclients snap

.. code-block:: bash

   sudo snap install openstackclients --channel xena/stable

2. Generate and source the openrc file.  (This example requires ``jq`` to be installed.)

.. code-block:: bash

    juju run-action --wait keystone/leader get-admin-account --format json | jq -r '.[].results.openrc' > openrc
    source ./openrc

3. Run some openstack commands

.. code-block:: bash

   openstack endpoint list

Launching a VM requires ``openstack-hypervisor`` and
``openstack-network-agents`` in the machine model alongside MicroOVN. They are
not part of these Kubernetes bundles.


.. _`juju with microk8s cloud`: https://juju.is/docs/olm/microk8s
