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
from dataclasses import (
    FrozenInstanceError,
)

from utils.validators import (
    validated_schedule,
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
            schedule = validated_schedule(exp)
            self.assertTrue(schedule.valid)
            self.assertEqual(schedule.err, "")
            self.assertEqual(schedule.value, exp)

    def test_expression_too_fast(self):
        """Verify an expression with an interval too fast is caught."""
        exp = "*/5 * * * *"
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("faster than every 15 minutes", schedule.err)
        self.assertEqual(schedule.value, exp)

    def test_expression_too_fast_edge_cases(self):
        """Verify an expression with intervals near edge cases are caught."""
        exp = "*/14 * * * *"
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("faster than every 15 minutes", schedule.err)
        self.assertEqual(schedule.value, exp)
        exp = "*/15 * * * *"
        schedule = validated_schedule(exp)
        self.assertTrue(schedule.valid)
        self.assertEqual(schedule.err, "")
        self.assertEqual(schedule.value, exp)

    def test_expression_six_fields(self):
        """Verify an expression with six fields is caught."""
        exp = "*/30 * * * * 6"
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("not support seconds", schedule.err)
        self.assertEqual(schedule.value, exp)

    def test_expression_missing_column(self):
        """Verify an expression with a missing field is caught."""
        exp = "*/30 * *"
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("Exactly 5 columns", schedule.err)
        self.assertEqual(schedule.value, exp)

    def test_expression_invalid_day(self):
        """Verify an expression with an invalid day field is caught."""
        exp = "*/25 * * * xyz"
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("not acceptable", schedule.err)
        self.assertEqual(schedule.value, exp)

    def test_expression_too_sparse(self):
        """Verify an expression with a very long period is caught."""
        exp = "0 4 30 2 *"  # on february 30  ;)
        schedule = validated_schedule(exp)
        self.assertFalse(schedule.valid)
        self.assertIn("not calculate a range", schedule.err)
        self.assertEqual(schedule.value, exp)

    def test_schedule_type_is_immutable(self):
        """Schedule should be immutable."""
        # this is both to avoid issues with caching it,
        # and to ensure a validated schedule is not accidentally modified
        # (it should not be modified because then it may not be valid any more)
        schedule = validated_schedule("5 4 * * *")
        self.assertTrue(schedule.valid)
        with self.assertRaises(FrozenInstanceError):
            schedule.valid = False
