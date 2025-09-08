# Copyright 2025 Canonical Ltd.
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

"""Schemas used by the openstack-hypervisor charm."""

DPDK_CONFIG_OVERRIDE_SCHEMA = """
type: object
required: [dpdk]
additionalProperties: false
properties:
    dpdk:
        type: object
        required: [dpdk-enabled]
        additionalProperties: false
        properties:
            dpdk-enabled:
                type: boolean
            dpdk-memory:
                type: integer
            dpdk-datapath-cores:
                type: integer
            dpdk-controlplane-cores:
                type: integer
            dpdk-driver:
                type: string
"""
