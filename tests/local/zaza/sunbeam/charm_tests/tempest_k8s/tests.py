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

import re
import json
import logging
import subprocess
import unittest
from random import shuffle
from typing import Tuple

import requests
import tenacity
import zaza.model as model
import zaza.openstack.charm_tests.test_utils as test_utils


class TempestK8sTest(test_utils.BaseCharmTest):
    """Charm tests for tempest-k8s."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(TempestK8sTest, cls).setUpClass(
            application_name="tempest"
        )

    def test_get_lists(self):
        """Verify that the get-lists action returns list names as expected."""
        action = model.run_action_on_leader(
            self.application_name, "get-lists"
        )
        lists = action.data["results"]["stdout"].splitlines()
        self.assertIn("readonly-quick", lists)
        self.assertIn("refstack-2022.11", lists)

    def test_validate_with_readonly_quick_tests(self):
        """Verify that the validate action runs tests as expected."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "test-list": "readonly-quick",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 23 tests", summary)
        self.assertIn("Passed: 19", summary)
        self.assertIn("Skipped: 4", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)

    def test_validate_with_readonly_quick_tests_regex(self):
        """Verify that the validate action runs tests with filter."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "test-list": "readonly-quick",
                "regex": "test.*show.extensions",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 1 tests", summary)
        self.assertIn("Passed: 1", summary)
        self.assertIn("Skipped: 0", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)

    def test_validate_with_readonly_quick_tests_regex_multiple(self):
        """Verify that the validate action runs tests with multiple regex."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "test-list": "readonly-quick",
                # NOTE: some regexes in these tests are a bit arbitrary,
                # just to double check that it's regex matching,
                # and not just substring for example.
                "regex": "test.*show.extensions test.*flavors_detailed.+marker",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 2 tests", summary)
        self.assertIn("Passed: 2", summary)
        self.assertIn("Skipped: 0", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)

    def test_validate_serial(self):
        """Verify that the validate action runs serially as expected."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "test-list": "readonly-quick",
                "serial": True,
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 23 tests", summary)
        self.assertIn("Passed: 19", summary)
        self.assertIn("Skipped: 4", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)

        # TODO: when https://github.com/juju/python-libjuju/issues/1029 is resolved,
        # pull the validation log file and verify that it was run on a single worker.
        # OR shell out to juju cli, pull the file to a temporary location.

    def test_validate_with_readonly_quick_tests_exclude_regex(self):
        """Verify that the validate action runs tests with exclude regex."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "test-list": "readonly-quick",
                "exclude-regex": "test_repository|share_networks|list_flavors|api_discovery|network\.test|test_versions.Versions|test.*scenar",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 3 tests", summary)
        self.assertIn("Passed: 3", summary)
        self.assertIn("Skipped: 0", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)

    def test_validate_with_regex_only(self):
        """Verify that the validate action with only regex selects from all."""
        action = model.run_action_on_leader(
            self.application_name, "validate",
            action_params={
                "regex": "[t]empest.api.compute.flavors.test_flavors.FlavorsV2TestJSON.test_get_flavor",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 1 tests", summary)
        self.assertIn("Passed: 1", summary)
        self.assertIn("Skipped: 0", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)
