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
"""Utilities for validating."""
from dataclasses import (
    dataclass,
)
from datetime import (
    datetime,
)

from croniter import (
    croniter,
)


@dataclass
class Schedule:
    """A cron schedule that has validation information."""

    value: str
    valid: bool
    err: str


def validated_schedule(schedule: str) -> Schedule:
    """Process and validate a schedule str.

    Return the schedule with validation info.
    """
    # Empty schedule is fine; it means it's disabled in this context.
    if not schedule:
        return Schedule(value=schedule, valid=True, err="")

    # croniter supports second repeats, but vixie cron does not.
    if len(schedule.split()) == 6:
        return Schedule(
            value=schedule,
            valid=False,
            err="This cron does not support seconds in schedule (6 fields). "
            "Exactly 5 columns must be specified for iterator expression.",
        )

    # constant base time for consistency
    base = datetime(2004, 3, 5)

    try:
        cron = croniter(schedule, base, max_years_between_matches=1)
    except ValueError as e:
        msg = str(e)
        # croniter supports second repeats, but vixie cron does not,
        # so update the error message here to suit.
        if "Exactly 5 or 6 columns" in msg:
            msg = (
                "Exactly 5 columns must be specified for iterator expression."
            )
        return Schedule(value=schedule, valid=False, err=msg)

    # This is a rather naive method for enforcing this,
    # and it may be possible to craft an expression
    # that results in some consecutive runs within 15 minutes,
    # however this is fine, as there is process locking for tempest,
    # and this is more of a sanity check than a security requirement.
    t1 = cron.get_next()
    t2 = cron.get_next()
    if t2 - t1 < 15 * 60:  # 15 minutes in seconds
        return Schedule(
            value=schedule,
            valid=False,
            err="Cannot schedule periodic check to run faster than every 15 minutes.",
        )

    return Schedule(value=schedule, valid=True, err="")
