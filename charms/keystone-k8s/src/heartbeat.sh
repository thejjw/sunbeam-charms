#!/usr/bin/env bash
#
# Copyright 2022 Canonical Ltd.
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


# This dispatches a custom hook event
# to the charm periodically.
# This regular event is used by the charm
# to schedule and run fernet key rotations.

# This script is designed to be launched by the charm on installation,
# and remain running for the life of the container.

# juju-run will fail if the context id is set.
unset JUJU_CONTEXT_ID

while true; do
    sleep $((60 * 5))  # 5 minutes
    juju-run -u "" JUJU_DISPATCH_PATH=hooks/heartbeat ./dispatch
done
