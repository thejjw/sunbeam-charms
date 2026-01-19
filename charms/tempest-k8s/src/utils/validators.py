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
from functools import (
    lru_cache,
)

from croniter import (
    CroniterBadDateError,
    croniter,
)


@dataclass(frozen=True)
class Schedule:
    """A cron schedule that has validation information."""

    value: str
    valid: bool
    err: str
    # in validation, these are the maximum and minimum intervals between runs seen
    max_interval: int = 0  # in seconds
    min_interval: int = 0  # in seconds


@lru_cache
def validated_schedule(schedule: str) -> Schedule:
    """Process and validate a schedule str.

    Return the schedule with validation info.

    Part of validation includes sampling a range of matches
    for the cron schedule.  This can be time consuming,
    so this function is cached to avoid repeating work.
    """
    # Empty schedule is fine; it means it's disabled in this context.
    if not schedule:
        return Schedule(value=schedule, valid=True, err="")

    # croniter supports more fields, but vixie cron does not.
    if len(schedule.split()) != 5:
        return Schedule(
            value=schedule,
            valid=False,
            err="This cron only support Vixie cron in schedule (5 fields). "
            "Exactly 5 columns must be specified for iterator expression.",
        )

    # constant base time for consistency
    base = datetime(2004, 3, 5)

    try:
        cron = croniter(schedule, base, max_years_between_matches=1)
    except ValueError as e:
        msg = str(e)
        # croniter supports more fields, but vixie cron does not,
        # so update the error message here to suit.
        if croniter.bad_length in msg:
            msg = (
                "Exactly 5 columns must be specified for iterator expression."
            )
        return Schedule(value=schedule, valid=False, err=msg)

    # This is a heuristic method of checking because cron schedules aren't regular,
    # and it may be possible to craft an expression
    # that results in some consecutive runs within 15 minutes,
    # however this is fine, as there is process locking for tempest,
    # and this is more of a sanity check than a security requirement.
    intervals = []  # in seconds
    try:
        last = cron.get_next()
        for _ in range(5):
            next_ = cron.get_next()
            intervals.append(next_ - last)
            last = next_
    except CroniterBadDateError:
        return Schedule(
            value=schedule,
            valid=False,
            err=(
                "Could not calculate a range of values from the schedule; "
                "please check the schedule or try a shorter schedule period."
            ),
        )

    if min(intervals) < 15 * 60:  # 15 minutes in seconds
        return Schedule(
            value=schedule,
            valid=False,
            err="Cannot schedule periodic check to run faster than every 15 minutes.",
        )

    return Schedule(
        value=schedule,
        valid=True,
        err="",
        max_interval=max(intervals),
        min_interval=min(intervals),
    )
