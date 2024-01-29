#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.

import pathlib
import zipfile
import yaml

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: charm_metadata
author: Canonical
description: Read charm metadata from a charm artifact

options:
  path:
    description: path to charm artifact
    required: yes
"""

EXAMPLES = r"""
- name: Read charm metadata
  charm_metadata:
    charm: /tmp/keystone.charm
"""


def run_module():
    module = AnsibleModule(
        argument_spec=dict(
            path=dict(type="str", required=True),
        )
    )

    path: str = module.params["path"]  # type: ignore
    charm_path = pathlib.Path(path)
    if not charm_path.exists():
        module.fail_json(msg=f"charm {path} not found on disk")
    
    with zipfile.ZipFile(charm_path, "r") as charm_zip:
        metadata = charm_zip.read("metadata.yaml")
    metadata_dict = yaml.safe_load(metadata)
    
    module.exit_json(changed=False, metadata=metadata_dict)


if __name__ == "__main__":
    run_module()
