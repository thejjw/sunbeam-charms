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

"""Unit tests for Tempest validator utility functions."""

import unittest

from utils.validators import (
    get_schedule_error,
    is_schedule_valid,
)


class TempestCharmValidatorTests(unittest.TestCase):
    """Test validator functions."""

    def test_valid_cron_expressions(self):
        """Verify valid cron expressions are marked as valid."""
        expressions = [
            "5 4 * * *",  # daily at 4:05
            "*/30 * * * *",  # every 30 minutes
            "5 2 * * *",  # at 2:05am every day
            "5 2 * * mon",  # at 2:05am every Monday
            "",  # empty = disabled, and is ok
        ]
        for exp in expressions:
            assert is_schedule_valid(exp)
            assert get_schedule_error(exp) is None

    def test_expression_too_fast(self):
        """Verify an expression with an interval too fast is caught."""
        exp = "*/5 * * * *"
        assert not is_schedule_valid(exp)
        assert "faster than every 15 minutes" in get_schedule_error(exp)

    def test_expression_too_fast_edge_cases(self):
        """Verify an expression with intervals near edge cases are caught."""
        exp = "*/14 * * * *"
        assert not is_schedule_valid(exp)
        assert "faster than every 15 minutes" in get_schedule_error(exp)
        exp = "*/15 * * * *"
        assert is_schedule_valid(exp)
        assert get_schedule_error(exp) is None

    def test_expression_six_fields(self):
        """Verify an expression with six fields is caught."""
        exp = "*/30 * * * * 6"
        assert not is_schedule_valid(exp)
        assert "not support seconds" in get_schedule_error(exp)

    def test_expression_missing_column(self):
        """Verify an expression with a missing field is caught."""
        exp = "*/30 * *"
        assert not is_schedule_valid(exp)
        assert "Exactly 5 columns" in get_schedule_error(exp)

    def test_expression_invalid_day(self):
        """Verify an expression with an invalid day field is caught."""
        exp = "*/25 * * * xyz"
        assert not is_schedule_valid(exp)
        assert "not acceptable" in get_schedule_error(exp)
