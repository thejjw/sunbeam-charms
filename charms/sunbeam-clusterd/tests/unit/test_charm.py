# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

"""Unit tests."""

import pathlib
from unittest.mock import (
    patch,
)

import charm
import ops
import ops.testing as testing
import ops_sunbeam.test_utils as test_utils
import yaml


class _SunbeamClusterdCharm(charm.SunbeamClusterdCharm):
    """Clusterd test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


charmcraft = (
    pathlib.Path(__file__).parents[2] / "charmcraft.yaml"
).read_text()
config = yaml.dump(yaml.safe_load(charmcraft)["config"])
actions = yaml.dump(yaml.safe_load(charmcraft)["actions"])


class TestCharm(test_utils.CharmTestCase):
    """Test the charm."""

    PATCHES = ["snap", "clusterd"]

    def setUp(self):
        """Setup charm and harness."""
        super().setUp(charm, self.PATCHES)
        self.harness = testing.Harness(
            _SunbeamClusterdCharm,
            meta=charmcraft,
            config=config,
            actions=actions,
        )
        ensure_snap_present = patch(
            "charm.SunbeamClusterdCharm.ensure_snap_present"
        )
        self.ensure_snap_present = ensure_snap_present.start()
        self.addCleanup(ensure_snap_present.stop)
        self.addCleanup(self.harness.cleanup)

    def initial_setup(self):
        """Common setup code for charm tests."""
        self.harness.add_network("10.0.0.10")
        self.harness.begin_with_initial_hooks()

    def test_initial_bootstrap(self):
        """Test charm is bootstrapped."""
        self.initial_setup()
        self.harness.set_leader()
        self.harness.charm.on.config_changed.emit()

        self.harness.evaluate_status()
        self.assertEqual(self.harness.charm.unit.status, ops.ActiveStatus())
        self.ensure_snap_present.assert_called()
        self.harness.charm._clusterd.bootstrap.assert_called_once()

    def test_initial_bootstrap_no_leader(self):
        """Test charm is bootstrapped."""
        self.initial_setup()
        self.harness.charm.on.config_changed.emit()
        self.assertEqual(
            self.harness.charm.unit.status,
            ops.WaitingStatus("(workload) Leader not ready"),
        )
        self.harness.charm._clusterd.bootstrap.assert_not_called()

    def test_config(self):
        """Test config update."""
        self.initial_setup()
        self.harness.set_leader()
        self.harness.update_config({"snap-channel": "edge"})
        self.ensure_snap_present.assert_called()

    def test_get_credentials(self):
        """Test get credentials action."""
        self.initial_setup()
        self.harness.set_leader()
        self.harness.charm.on.config_changed.emit()

        output = self.harness.run_action("get-credentials")
        self.assertEqual({"url": "https://10.0.0.10:7000"}, output.results)
