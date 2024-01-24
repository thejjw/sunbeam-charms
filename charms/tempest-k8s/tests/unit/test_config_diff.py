#!/usr/bin/env python3

# Copyright 2023 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unit tests for tempest.conf diff tool."""
import unittest

from utils.config_diff import (
    diff_tempest_conf,
)


class Tests(unittest.TestCase):
    """Test tempest.conf differ tool."""

    def test_the_same(self):
        """Verify no results are reported if config is logically the same."""
        old = """
[DEFAULT]

# comments
log_rotate_interval_type = days
max_logfile_count = 30
[auth]
admin_username = admin
admin_password = mysecret
"""

        new = """
[auth]

admin_password = mysecret
admin_username = admin
[DEFAULT]

# comments sholudn't create a diff
log_rotate_interval_type = days
max_logfile_count = 30
"""
        assert diff_tempest_conf(old, new) == ""
