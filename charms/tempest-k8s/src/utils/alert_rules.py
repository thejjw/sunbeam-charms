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
import yaml

ALERT_RULES_PATH = "src/loki_alert_rules"


def update_alert_rules_files(schedule: str) -> None:
    """Update files for alert rules based on the schedule.

    `schedule` is expected to be a validated cron schedule.
    This is used for configuring the time range for the alert for absent tests.
    """

    # TODO: if schedule is empty, delete the rules file and return

    # TODO: calculate the average interval between periodic tests,
    # cap it to a maximum,
    # template it to the AbsentTests alert

    rules = {
        "groups": [
            {
                "name": "tempest-failed-tests",
                "rules": [
                    {
                        "alert": "FailedTests",
                        # 23d is the max interval here; the aim is to get an accurate alert even if the periodic tests are scheduled infrequently.
                        # TODO: is this interval configurable or hardcoded in grafana?  Can we assume this is fine?
                        "expr": 'last_over_time({filename="/var/lib/tempest/workspace/tempest-periodic.log", %%juju_topology%%} |~ "- Failed:" | pattern " - <_>: <number_of_tests>" | unwrap number_of_tests [23d]) > 0',
                        "labels": {
                            "severity": "high",
                        },
                    },
                    {
                        "alert": "AbsentTests",
                        "expr": f'absent_over_time({filename="/var/lib/tempest/workspace/tempest-periodic.log", %%juju_topology%%} |~ "- Failed:" | pattern " - <_>: <number_of_tests>" | unwrap number_of_tests[{TODO}]) == 1',
                        "labels": {
                            "severity": "high",
                        },
                        "annotations": {
                            "summary": "Tempest periodic tests not seen recently",
                        },
                    },
                ]
            }
        ]
    }

    # TODO: save the file to ALERT_RULES_PATH

    # TODO: remember docs, and document the known issues / assumptions (max range, etc.)
