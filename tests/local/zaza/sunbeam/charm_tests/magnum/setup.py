# Copyright (c) 2025 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import re

import jubilant
import zaza.model


def configure_at_model(model: str):
    """Configure Magnum at a given model."""
    application = "magnum"
    secret_name = "kubeconfig"
    secret_content = {"kubeconfig": "fake-kubeconfig"}
    secret_not_found_pattern = r'ERROR secret ".*" not found'
    secret_uri: jubilant.secrettypes.SecretURI

    logging.debug(f"Magnum configure: Using model {model}")
    juju = jubilant.Juju(model=model)

    create_secret = False
    try:
        kubeconfig_secret = juju.show_secret(identifier=secret_name)
        secret_uri = kubeconfig_secret.uri
        logging.debug(f"Juju secret {secret_name} found")
    except jubilant.CLIError as e:
        match = re.search(secret_not_found_pattern, e.stderr)
        if not match:
            raise

        create_secret = True

    if create_secret:
        logging.debug(f"Create juju secret {secret_name}")
        secret_uri = juju.add_secret(name=secret_name, content=secret_content)
        juju.grant_secret(secret_uri, application)

    logging.info(f"Setting {application} kubeconfig option")
    juju.config(app=application, values={"kubeconfig": secret_uri})
    logging.info(f"Waiting for application {application} to be active")
    juju.wait(
        lambda status: jubilant.all_active(status, application),
        timeout=180,
    )


def configure():
    """Setup any configurations required by Magnum.

    Setup kubeconfig configuration parameter by adding a juju secret.
    """
    model = zaza.model.get_juju_model()
    configure_at_model(model)
