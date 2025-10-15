# Copyright 2025 Canonical Ltd.
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

"""Utils for creating accounts for tempest.

The tempest account-generator command does not generate system accounts,
and they are needed for the Ironic tempest tests.

Based on:
https://opendev.org/openstack/tempest/src/commit/b15ff9b0f28b0a3de70f02c05d107acf589d758d/tempest/cmd/account_generator.py
"""

import os
import traceback

import yaml
from oslo_log import log as logging
from tempest import (
    config,
)
from tempest.common import (
    credentials_factory,
)
from tempest.lib.common import (
    dynamic_creds,
)

LOG = None
CONF = config.CONF


def setup_logging():
    """Setup logging."""
    global LOG
    logging.setup(CONF, __name__)
    LOG = logging.getLogger(__name__)


def get_credential_provider():
    """Returns a credential provider using the environment variables."""
    # NOTE(andreaf) For now tempest.conf controls whether resources will
    # actually be created. Once we remove the dependency from tempest.conf
    # we will need extra CLI option(s) to control this.
    network_resources = {
        "router": True,
        "network": True,
        "subnet": True,
        "dhcp": True,
    }
    admin_creds_dict = {
        "username": os.getenv("OS_USERNAME", ""),
        "password": os.getenv("OS_PASSWORD", ""),
    }

    project_name = os.getenv("OS_PROJECT_NAME", "")
    auth_version = os.getenv("OS_AUTH_VERSION", 3)
    identity_version = f"v{auth_version}"

    if identity_version == "v3":
        admin_creds_dict["project_name"] = project_name
        admin_creds_dict["domain_name"] = os.getenv("OS_DOMAIN_NAME", "Default")
    elif identity_version == "v2":
        admin_creds_dict['tenant_name'] = project_name

    admin_creds = credentials_factory.get_credentials(
        fill_in=False,
        identity_version=identity_version,
        **admin_creds_dict,
    )
    params = credentials_factory.get_dynamic_provider_params(
        identity_version,
        admin_creds=admin_creds,
    )

    return dynamic_creds.DynamicCredentialProvider(
        network_resources=network_resources,
        **params,
    )


def generate_resources(cred_provider) -> list[dict]:
    """Creates accounts using the given credentials provider."""
    # Create the list of resources to be provisioned for each process
    # NOTE(andreaf) get_credentials expects a string for types or a list for
    # roles. Adding all required inputs to the spec list.
    extra_roles = CONF.auth.tempest_roles or []
    spec = ["primary", "alt", "project_reader"]

    if CONF.service_available.swift:
        spec.append([CONF.object_storage.operator_role])
        spec.append([CONF.object_storage.reseller_admin_role])

    if CONF.service_available.ironic:
        spec.append("admin")
        spec.append("project_admin")
        spec.append("project_member")
        spec.append("system_admin")
        spec.append("system_reader")

    resources = []
    for cred_type in spec:
        scope = None
        if "_" in cred_type:
            scope = cred_type.split("_")[0]
            cred_type = cred_type.split("_")[1:2]

        # NOTE(claudiub): The credentials_factory.get_dynamic_provider_params
        # return also includes {"extra_roles": CONF.auth.tempest_roles}.
        # We're setting the following config option in tempest.conf:
        # [auth]
        # tempest_roles = member
        #
        # This means that all the credentials we're creating will include the
        # member role by default.
        # This can be an issue for some RBAC-related tests (e.g.: Ironic RBAC
        # tests), as they expect readers to not be able to do certain
        # operations, but they are able because they have the member role.
        if cred_type == ["reader"]:
            cred_provider.extra_roles = []
        else:
            cred_provider.extra_roles = extra_roles

        resource = cred_provider.get_credentials(
            credential_type=cred_type,
            scope=scope,
        )
        resources.append(
            {
                "resource": resource,
                "cred_type": cred_type,
                "scope": scope,
            }
        )

    return resources


def dump_accounts(resources, account_file):
    """Appends the given accounts to the given account_file."""
    identity_version = int(os.getenv("OS_AUTH_VERSION", "3"))

    accounts = []
    for resource_dict in resources:
        resource = resource_dict["resource"]
        cred_type = resource_dict["cred_type"]
        scope = resource_dict["scope"]

        account = {
            "username": resource.credentials.username,
            "password": resource.credentials.password,
        }
        if scope == "system":
            account["system"] = "all"
            account["user_domain_name"] = resource.credentials.user_domain_name
        elif identity_version == 3:
            account["project_name"] = resource.credentials.project_name
            account["domain_name"] = resource.credentials.domain_name
        else:
            account["project_name"] = resource.credentials.project_name

        # If the spec includes 'admin' credentials are defined via type,
        # else they are defined via list of roles.
        if cred_type == "admin":
            account["types"] = [cred_type]
        elif cred_type not in ["primary", "alt"]:
            account["roles"] = cred_type

        if resource.network:
            account["resources"] = {}
            account["resources"]["network"] = resource.network["name"]

        accounts.append(account)

    if os.path.exists(account_file):
        os.rename(account_file, ".".join((account_file, "bak")))

    with open(account_file, "w") as f:
        yaml.safe_dump(accounts, f, default_flow_style=False)

    LOG.info('%s generated successfully!', account_file)


def main() -> None:
    """Entrypoint for executing the script directly."""
    tempest_conf = os.getenv("TEMPEST_CONF", "")
    test_accounts_file = os.getenv("TEMPEST_TEST_ACCOUNTS", "")
    test_accounts_count = int(os.getenv("TEMPEST_ACCOUNTS_COUNT", 8))

    if not tempest_conf:
        raise RuntimeError("Expected TEMPEST_CONF env variable.")
    if not test_accounts_file:
        raise RuntimeError("Expected TEMPEST_TEST_ACCOUNTS env variable.")

    try:
        config.CONF.set_config_path(tempest_conf)
        setup_logging()

        resources = []
        for i in range(test_accounts_count):
            # Use N different cred_providers to obtain different sets of creds.
            cred_provider = get_credential_provider()
            resources.extend(generate_resources(cred_provider))

        dump_accounts(resources, test_accounts_file)
    except Exception:
        LOG.exception("Failure generating test accounts.")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
