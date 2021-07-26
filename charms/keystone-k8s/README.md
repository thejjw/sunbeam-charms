# keystone-operator

## Description

The keystone operator is an operator to manage the keystone identity
service.

## Usage

TODO: Provide high-level usage, such as required config or relations


## Developing

This project uses tox for building and managing. To build the charm
run:

    tox -e build

To deploy the local test instance:

    tox -e build
    juju add-model keystone
    juju deploy ./keystone-operator.charm --resource keystone-image=kolla/ubuntu-binary-keystone:victoria


## Status

This charm is currently in basic dev/exploratory state. This charm will deploy a keystone instance which uses local sqlite database.

TODOs

- [X] Basic bootstrap of keystone service
- [ ] Support database relations
  - [X] MySQL K8s relation
  - [ ] Handle shared db relation
- [ ] Fernet Token Rotation
- [ ] Ingress
- [ ] Provide identity-service relation
- [ ] Handle config changed events
- [ ] Unit tests
- [ ] Functional tests

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

    ./run_tests
