#!/usr/bin/env python3
#
# Copyright 2023 Canonical Ltd.
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


"""Render all smoke bundles.

Renders smoke bundles with context of locally built charms.
Prepares the context with assumption the charm is locally
built if corresponding *.charm exists in current folder.

Assumption: All build charms will be in sunbeam-charms folder.
"""

import glob
from pathlib import (
    Path,
)

from jinja2 import (
    Environment,
    FileSystemLoader,
)

test_directories = [dir_.name for dir_ in list(Path("tests").glob('*')) if dir_.name != "local"]
built_charms = glob.glob("*.charm")
context = {
    charm.replace(".charm", "").replace("-", "_"): True for charm in built_charms
}
print(f"Using context: {context}")

for test_dir in test_directories:
    bundle_dir = f"tests/{test_dir}"
    template_loader = Environment(loader=FileSystemLoader(bundle_dir))
    templates = [pth.name for pth in Path(bundle_dir).glob('*.yaml.j2')]
    for tpl in templates:
        bundle_template = template_loader.get_template(tpl)
        bundle_file = Path(f"{bundle_dir}/bundles/{tpl[:-3]}")
        bundle_file.parent.mkdir(parents=True, exist_ok=True)
        with bundle_file.open("w", encoding="utf-8") as content:
            content.write(bundle_template.render(context))
            print(f"Rendered bundle: {bundle_file}")
