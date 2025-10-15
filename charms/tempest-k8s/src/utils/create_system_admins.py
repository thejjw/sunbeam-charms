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

"""Utils for creating system admin accounts for tempest.

The tempest account-generator command does not generate system admin accounts,
and they are needed for the Ironic tempest tests.
"""

import os
import yaml

from tempest import config
from tempest.common import credentials_factory
from tempest.lib.common import dynamic_creds


def get_credential_provider():
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

    return dynamic_creds.DynamicCredentialProvider(**params)


def append_system_accounts(resources, accounts_file):
    accounts = []
    if os.path.exists(accounts_file):
        with open(accounts_file, "r") as f:
            accounts = yaml.safe_load(f)

    for resource in resources:
        # based on: https://github.com/openstack/tempest/blob/93df2d2d3e73788db982be0f4b85e3451343c94c/etc/accounts.yaml.sample#L57-L64
        account = {
            "username": resource.credentials.username,
            "password": resource.credentials.password,
            "user_domain_name": resource.credentials.user_domain_name,
            "system": "all",
            "roles": ["admin"],
        }
        accounts.append(account)

    with open(accounts_file, "w") as f:
        yaml.safe_dump(accounts, f, default_flow_style=False)

    print(f"System accounts appended to {accounts_file} successfully!")


def main() -> None:
    """Entrypoint for executing the script directly."""
    tempest_conf = os.getenv("TEMPEST_CONF", "")
    test_accounts_file = os.getenv("TEMPEST_TEST_ACCOUNTS", "")
    test_accounts_count = int(os.getenv("TEMPEST_ACCOUNTS_COUNT", 8))

    if not tempest_conf:
        raise RuntimeError("Expected TEMPEST_CONF env variable.")
    if not test_accounts_file:
        raise RuntimeError("Expected TEMPEST_TEST_ACCOUNTS env variable.")

    config.CONF.set_config_path(tempest_conf)

    cred_provider = get_credential_provider()
    resources = []
    for i in range(test_accounts_count):
        resource = cred_provider.get_credentials(
            credential_type=["admin"],
            scope="system",
        )
        resources.append(resource)

    append_system_accounts(resources, test_accounts_file)


if __name__ == "__main__":
    main()
