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

from multiprocessing import (
    cpu_count,
)


def get_tempest_concurrency() -> str:
    """Return the concurrency for tempest.

    4 is chosen as a constant small number,
    to avoid overloading the cloud and/or the host machine.
    If less cpu cores are available,
    the concurrency value must be bounded by that,
    otherwise performance will definitely suffer.

    Note that this will be run in a k8s container,
    so this could reflect the host machine's number of cores.
    """
    return str(min(4, cpu_count()))


# It's the target location for the OS cacert template file.  See
# https://opendev.org/openstack/sunbeam-charms/src/branch/main/templates/ca-bundle.pem.j2
OS_CACERT_PATH = "/usr/local/share/ca-certificates/ca-bundle.pem"
# The relation name of 'receive-ca-cert'. See
# https://opendev.org/openstack/sunbeam-charms/src/branch/main/ops-sunbeam/ops_sunbeam/charm.py#L194
RECEIVE_CA_CERT_RELATION_NAME = "receive-ca-cert"

TEMPEST_CONCURRENCY = get_tempest_concurrency()

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
# This is used to exclude some tests that are known to fail in tempest-k8s
# because of needing admin credentials.
TEMPEST_EXCLUDE_LIST = f"{TEMPEST_HOME}/tempest_exclude_list.txt"
# keys for application data
TEMPEST_READY_KEY = "tempest-ready"

CONTAINER = "tempest"

# ==============================================================================
# Do not change the following values as we need to keep using the same names to
# do clean-ups on previously created domain, user, and project via identity-ops
# relation.
# ==============================================================================
# hash suffix is added to avoid possible collision with user created domains
OPENSTACK_DOMAIN = "CloudValidation-b82746a08d"
# not use tempest as prefix to exclude this project from utils/cleanup.py scope
OPENSTACK_PROJECT = "CloudValidation-test-project"
OPENSTACK_USER = "CloudValidation-test-user"
OPENSTACK_ROLE = "admin"
