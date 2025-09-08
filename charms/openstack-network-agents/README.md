# OpenStack Network Agents (charm)

This subordinate charm drives the `openstack-network-agents` snap on
Sunbeam network-role nodes. It configures the provider bridge (`br-ex`),
OVN physnet mapping (`physnet1`) and configures the node to act as
a gateway via `enable-chassis-as-gw` option.

## Usage

Attach to MicroOVN units:

```bash
juju relate openstack-network-agents:juju-info microovn:juju-info
```

## Configure

```bash
juju config openstack-network-agents \
  external-interface=enp86s0 \
  bridge-name=br-ex \
  physnet-name=physnet1
```
