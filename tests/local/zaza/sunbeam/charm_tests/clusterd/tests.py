# Copyright (c) 2024 Canonical Ltd.
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

import base64
import contextlib
import json
import logging
import subprocess
import tempfile
import unittest
from random import shuffle
from typing import Tuple

import requests
import requests.adapters
import tenacity
import zaza
import zaza.model as model
import zaza.openstack.charm_tests.test_utils as test_utils
from juju.client import client
from juju.model import Model


@contextlib.contextmanager
def keypair(certificate: bytes, private_key: bytes):
    with tempfile.NamedTemporaryFile() as cert_file, tempfile.NamedTemporaryFile() as key_file:
        cert_file.write(certificate)
        cert_file.flush()
        key_file.write(private_key)
        key_file.flush()
        yield (cert_file.name, key_file.name)


class ClusterdTest(test_utils.BaseCharmTest):
    """Charm tests for clusterd."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(ClusterdTest, cls).setUpClass(
            application_name="sunbeam-clusterd"
        )

    def _get_units(self) -> list[str]:
        """Get the units."""
        return [unit.name for unit in model.get_units(self.application_name)]

    def _query_clusterd(self, unit: str, method: str, path: str):
        cmd = [
            "juju",
            "ssh",
            unit,
            "sudo",
            "curl",
            "-s",
            "--unix-socket",
            "/var/snap/openstack/common/state/control.socket",
            "-X",
            method,
            "http://localhost" + path,
        ]
        try:
            stdout = subprocess.check_output(cmd)
        except subprocess.CalledProcessError:
            logging.exception("Failed to query clusterd on %s", unit)
            self.fail("Failed to query clusterd on {}".format(unit))
        return json.loads(stdout.decode("utf-8"))

    def _add_2_units(self):
        model.add_unit(self.application_name, count=2)
        model.block_until_unit_count(self.application_name, 3)
        model.block_until_all_units_idle()
        units = self._get_units()
        for unit in units:
            model.block_until_unit_wl_status(unit, "active", timeout=60 * 5)

    async def _read_secret(
        self, model: Model, secret_id: str
    ) -> dict[str, str]:
        facade = client.SecretsFacade.from_connection(model.connection())
        secrets = await facade.ListSecrets(
            filter_={"uri": secret_id}, show_secrets=True
        )
        if len(secrets.results) != 1:
            self.fail("Secret not found")
        return secrets["results"][0].value.data

    def test_100_connect_to_clusterd(self):
        """Try sending data to an endpoint."""
        action = model.run_action_on_leader(
            self.application_name, "get-credentials"
        )
        url = action.data["results"]["url"] + "/1.0/config/100_connect"
        private_key_secret = action.data["results"].get("private-key-secret")
        certificate = action.data["results"].get("certificate")
        if private_key_secret is None or certificate is None:
            context = contextlib.nullcontext()
            logging.debug("Request made without mTLS")
        else:
            model_impl = zaza.sync_wrapper(model.get_model)()
            private_key = base64.b64decode(
                zaza.sync_wrapper(self._read_secret)(
                    model_impl, private_key_secret
                )["private-key"]
            )
            context = keypair(certificate.encode(), private_key)
            logging.debug("Request made with mTLS")

        with context as cert:
            response = requests.put(
                url, json={"data": "test"}, verify=False, cert=cert
            )
            response.raise_for_status()
            response = requests.get(url, verify=False, cert=cert)
            response.raise_for_status()

        self.assertEqual(
            json.loads(response.json()["metadata"])["data"], "test"
        )

    def test_200_scale_up(self):
        """Scale up."""
        self._add_2_units()

    @unittest.skip("Skip until scale down stable")
    def test_201_scale_down_multiple_units(self):
        """Scale down 2 units."""
        units = self._get_units()
        shuffle(units)
        model.destroy_unit(
            self.application_name, *units[:2], wait_disappear=True
        )
        model.block_until_all_units_idle()

        units = self._get_units()
        for unit in units:
            model.block_until_unit_wl_status(unit, "active", timeout=60 * 5)

    @unittest.skip("Skip until scale down stable")
    def test_202_scale_up_again(self):
        """Scale back to 3."""
        self._add_2_units()

    @unittest.skip("Skip until scale down stable")
    def test_203_scale_down_to_2_units(self):
        """Scale down to 2 units for voter/spare test."""
        leader = model.get_lead_unit_name(self.application_name)
        model.destroy_unit(self.application_name, leader, wait_disappear=True)
        model.block_until_all_units_idle()

        units = self._get_units()
        for unit in units:
            model.block_until_unit_wl_status(unit, "active", timeout=60 * 5)

    def _wait_for_voter_spare(
        self, unit: str, timeout=1800
    ) -> Tuple[str, str]:
        """After a scale down of microcluster, it can take a while for the
        voter, spare to be elected. This function will wait for these roles
        to be elected.
        """

        @tenacity.retry(
            wait=tenacity.wait_fixed(10),
            stop=tenacity.stop_after_delay(timeout),
            retry=tenacity.retry_if_exception_type(ValueError),
        )
        def _tenacity_handler() -> Tuple[str, str]:
            voter, spare = None, None
            output = self._query_clusterd(unit, "GET", "/core/1.0/cluster")
            metadata = output.get("metadata")
            if metadata is None:
                logging.warning("No metadata from clusterd, %r", output)
                raise ValueError("No metadata from clusterd")
            for member in output["metadata"]:
                if member["role"] == "voter":
                    voter = member["name"]
                elif member["role"] == "spare":
                    spare = member["name"]
            if voter is None or spare is None:
                raise ValueError("No voter or spare found")
            return voter, spare

        return _tenacity_handler()

    @unittest.skip("Skip until scale down stable")
    def test_204_scale_down_voter(self):
        """Scale down the voter member.

        When there's only 2 members left, 1 is voter, and 1 is spare.
        There has been issues when the voter member is removed.
        """
        units = self._get_units()
        voter, _ = self._wait_for_voter_spare(units[0])
        for unit in units:
            if unit.replace("/", "-") == voter:
                model.destroy_unit(
                    self.application_name,
                    unit,
                    wait_disappear=True,
                )
                units.remove(unit)
                break
        else:
            self.fail("No unit found for voter {}".format(voter))
        model.block_until_all_units_idle()
        model.block_until_unit_wl_status(units[0], "active", timeout=60 * 5)
        output = self._query_clusterd(units[0], "GET", "/core/1.0/cluster")
        self.assertEqual(output["status_code"], 200)
        self.assertEqual(len(output["metadata"]), 1)
