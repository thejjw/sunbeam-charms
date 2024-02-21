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
                "regex": "[V]ersionsTest.*",
                "exclude-regex": "show_vers.+",
            }
        )
        summary = action.data["results"]["summary"]

        # These are the expected results with the test bundle;
        self.assertIn("Ran: 2 tests", summary)
        self.assertIn("Passed: 1", summary)
        self.assertIn("Skipped: 1", summary)
        self.assertIn("Expected Fail: 0", summary)
        self.assertIn("Unexpected Success: 0", summary)
        self.assertIn("Failed: 0", summary)
