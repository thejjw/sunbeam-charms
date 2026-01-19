# Copyright 2024 Canonical Ltd.
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
"""Working with the loki logging alert rules."""

import os
from math import (
    ceil,
)

import yaml
from utils.validators import (
    Schedule,
)

ALERT_RULES_PATH = "src/loki_alert_rules"
ALERT_RULES_FILE = ALERT_RULES_PATH + "/tests.rules"

# The default for max_query_length in Loki is now 721h,
# and thus the value in Loki deployed by COS.
# ref. https://github.com/grafana/loki/issues/4509
# We need a small buffer to make it work in these queries.
MAX_RANGE_HOURS = 719


def ensure_alert_rules_disabled():
    """Ensure the alert rules files don't exist."""
    try:
        os.remove(ALERT_RULES_FILE)
    except FileNotFoundError:
        pass
    return


def update_alert_rules_files(schedule: Schedule) -> None:
    """Update files for alert rules based on the schedule.

    `schedule` is expected to be a valid and ready Schedule.
    """
    absent_range_hours = min(
        # Convert seconds to hours,
        # round up to avoid a range of 0,
        # and double the interval to ensure it only alerts when one was definitely missed.
        ceil(schedule.max_interval / 60 / 60) * 2,
        # Ensure that the log query limit isn't exceeded
        MAX_RANGE_HOURS,
    )

    rules = {
        "groups": [
            {
                "name": "tempest-failed-tests",
                "rules": [
                    {
                        "alert": "FailedTests",
                        "expr": f'last_over_time({{filename="/var/lib/tempest/workspace/tempest-periodic.log", %%juju_topology%%}} |~ "- Failed:" | pattern " - <_>: <number_of_tests>" | unwrap number_of_tests [{MAX_RANGE_HOURS}h]) > 0',
                        "labels": {
                            "severity": "high",
                        },
                        "annotations": {
                            "summary": "Tempest periodic tests failed.",
                        },
                    },
                    {
                        "alert": "AbsentTests",
                        "expr": f'absent_over_time({{filename="/var/lib/tempest/workspace/tempest-periodic.log", %%juju_topology%%}} |~ "- Failed:" [{absent_range_hours}h]) == 1',
                        "labels": {
                            "severity": "high",
                        },
                        "annotations": {
                            "summary": "Tempest periodic tests were not run on schedule.",
                        },
                    },
                ],
            }
        ]
    }

    with open(ALERT_RULES_FILE, "w") as f:
        yaml.safe_dump(rules, f)
