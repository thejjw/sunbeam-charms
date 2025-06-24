# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import subprocess

import zaza.model as model
import zaza.openstack.charm_tests.test_utils as test_utils


class EpaOrchestratorTest(test_utils.BaseCharmTest):
    """Charm tests for epa-orchestrator."""
    snap_name = "epa-orchestrator"

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(EpaOrchestratorTest, cls).setUpClass(
            application_name="epa-orchestrator"
        )

    def _get_units(self) -> list[str]:
        """Get the units."""
        return [unit.name for unit in model.get_units(self.application_name)]

    def _run_ssh_command(self, unit: str, command: list[str]):
        """Run a command on a unit via SSH."""
        cmd = ["juju", "ssh", unit] + command
        try:
            stdout = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            return stdout.decode("utf-8").strip()
        except subprocess.CalledProcessError as e:
            logging.exception("Failed to run command on %s: %s", unit, e.output)
            self.fail("Failed to run command on {}: {}".format(unit, e.output))

    def _check_snap_installed(self, unit: str, snap_name: str) -> bool:
        """Check if a snap is installed on the unit."""
        try:
            output = self._run_ssh_command(unit, ["snap", "list", snap_name])
            return snap_name in output
        except Exception:
            return False

    def test_100_snap_installed(self):
        """Test that the epa-orchestrator snap is installed."""
        units = self._get_units()
        for unit in units:
            with self.subTest(unit=unit):        
                logging.info("Checking if snap %s is installed on %s", self.snap_name, unit)
                self.assertTrue(
                    self._check_snap_installed(unit, self.snap_name),
                    f"Snap {self.snap_name} is not installed on {unit}"
                )

    def test_200_charm_status_active(self):
        """Test that all units are in active status."""
        units = self._get_units()
        for unit in units:
            with self.subTest(unit=unit):
                model.block_until_unit_wl_status(unit, "active", timeout=60 * 5)
                unit_status = model.get_unit_status(unit)
                self.assertEqual(
                    unit_status["workload-status"]["current"], 
                    "active",
                    f"Unit {unit} is not in active status"
                )