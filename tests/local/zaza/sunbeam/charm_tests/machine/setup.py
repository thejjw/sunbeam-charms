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
from pathlib import Path

import jubilant
import zaza.model

MACHINE_MODEL = "controller"


def replace_model_in_bundle(bundle: Path, words_to_replace: dict):
    content = bundle.read_text()
    for old_word, new_word in words_to_replace.items():
        logging.debug(f"Replacing {old_word} with {new_word}")
        modified_content = content.replace(old_word, new_word)

    bundle.write_text(modified_content)

def deploy_machine_applications():
    k8s_model = zaza.model.get_juju_model()

    logging.debug(f"Updating machine bundle")
    bundle = "./tests/all-k8s/bundles/machines.yaml"
    words_to_replace = {"K8S_MODEL": k8s_model}
    replace_model_in_bundle(Path(bundle), words_to_replace)

    logging.info(bundle)
    juju = jubilant.Juju(model=MACHINE_MODEL)
    juju.cli("deploy", str(bundle), "--map-machines=existing,0=0")
