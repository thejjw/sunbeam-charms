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
"""Constants for the tempest charm."""
CONTAINER = "tempest"

TEMPEST_CONCURRENCY = "4"
TEMPEST_HOME = "/var/lib/tempest"
TEMPEST_WORKSPACE_PATH = f"{TEMPEST_HOME}/workspace"
TEMPEST_CONF = f"{TEMPEST_WORKSPACE_PATH}/etc/tempest.conf"
TEMPEST_TEST_ACCOUNTS = f"{TEMPEST_WORKSPACE_PATH}/test_accounts.yaml"
TEMPEST_LIST_DIR = "/tempest_test_lists"
# this file will contain the output from tempest's latest test run
TEMPEST_PERIODIC_OUTPUT = f"{TEMPEST_WORKSPACE_PATH}/tempest-periodic.log"
TEMPEST_ADHOC_OUTPUT = f"{TEMPEST_WORKSPACE_PATH}/tempest-validation.log"
# This is the workspace name registered with tempest.
# It will be saved in a file in $HOME/.tempest/
TEMPEST_WORKSPACE = "tempest"

OPENSTACK_USER = "tempest"
OPENSTACK_DOMAIN = "tempest"
# not use tempest as prefix to exclude this project from utils/cleanup.py scope
OPENSTACK_PROJECT = "CloudValidation-tempest"
OPENSTACK_ROLE = "admin"

# keys for application data
TEMPEST_READY_KEY = "tempest-ready"
