# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manifest management for ovs-cni and openstack-port-cni."""

from typing import (
    Dict,
)

from ops.manifests import (
    ManifestLabel,
    Manifests,
)


class OvsCniManifests(Manifests):
    """Manages the upstream ovs-cni manifests.

    Upstream manifests live under ``upstream/ovs-cni/manifests/<release>/``.
    The active release is selected by the ``ovs-cni-release`` charm config key.
    """

    def __init__(self, charm, charm_config):
        manipulations = [ManifestLabel(self)]
        super().__init__(
            "ovs-cni",
            charm.model,
            "upstream/ovs-cni",
            manipulations,
        )
        self.charm_config = charm_config

    @property
    def config(self) -> Dict:
        """Return config dict consumed by ops.manifests manipulations."""
        config = dict(**self.charm_config)
        # Drop empty/None values so manipulations skip unset options.
        for key, value in list(config.items()):
            if value == "" or value is None:
                del config[key]
        # ops.manifests looks for the "release" key to select the manifest
        # subdirectory, e.g. upstream/ovs-cni/manifests/<release>/.
        config["release"] = config.pop("ovs-cni-release", None)
        return config


class OpenstackPortCniManifests(Manifests):
    """Manages the openstack-port-cni manifests.

    Upstream manifests live under
    ``upstream/openstack-port-cni/manifests/<release>/``.
    The active release is selected by the ``openstack-port-cni-release``
    charm config key.
    """

    def __init__(self, charm, charm_config):
        manipulations = [ManifestLabel(self)]
        super().__init__(
            "openstack-port-cni",
            charm.model,
            "upstream/openstack-port-cni",
            manipulations,
        )
        self.charm_config = charm_config

    @property
    def config(self) -> Dict:
        """Return config dict consumed by ops.manifests manipulations."""
        config = dict(**self.charm_config)
        for key, value in list(config.items()):
            if value == "" or value is None:
                del config[key]
        config["release"] = config.pop("openstack-port-cni-release", None)
        return config
