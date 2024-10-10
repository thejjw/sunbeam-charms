#!/usr/bin/env python3
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
"""sunbeam-libs Charm.

This charm is a placeholder for sunbeam common libraries.
"""

import ops_sunbeam.charm as sunbeam_charm
from ops import (
    main,
)


class SunbeamLibsCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """Placeholder charm for Sunbeam common libs."""

    @property
    def service_name(self):
        """Service name."""
        return "placeholder"


if __name__ == "__main__":
    main(SunbeamLibsCharm)
