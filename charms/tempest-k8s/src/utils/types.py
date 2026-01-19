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
"""Types for the tempest charm."""

from enum import (
    Enum,
)

from .constants import (
    TEMPEST_ADHOC_OUTPUT,
    TEMPEST_PERIODIC_OUTPUT,
)


class TempestEnvVariant(Enum):
    """Represent a variant of the standard tempest environment."""

    PERIODIC = 1
    ADHOC = 2

    def output_path(self) -> str:
        """Return the correct tempest output path."""
        return (
            TEMPEST_PERIODIC_OUTPUT
            if self.value == self.PERIODIC.value
            else TEMPEST_ADHOC_OUTPUT
        )
