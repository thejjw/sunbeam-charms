#!/usr/bin/env python3
#
# Copyright 2021 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
# Learn more at: https://juju.is/docs/sdk


"""Sunbeam Machine Charm.

This charm provide a place to add machine configuration and relate
subordinates that configure machine services.
"""

import logging

import ops.framework
import ops_sunbeam.charm as sunbeam_charm
from ops.main import main

logger = logging.getLogger(__name__)


class SunbeamMachineCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "sunbeam-machine"


if __name__ == "__main__":  # pragma: nocover
    main(SunbeamMachineCharm)
