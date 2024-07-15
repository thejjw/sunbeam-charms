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

test_directories = [
    dir_.name for dir_ in list(Path("tests").iterdir()) if dir_.name != "local"
]
built_charms = glob.glob("*.charm")
context = {
    charm.removesuffix(".charm").replace("-", "_"): True for charm in built_charms
}
print(f"Using context: {context}")

for test_dir in test_directories:
    bundle_dir = Path(f"tests/{test_dir}")
    template_loader = Environment(loader=FileSystemLoader(bundle_dir))
    for bundle in bundle_dir.glob("*.yaml.j2"):
        bundle_template = template_loader.get_template(bundle.name)
        smoke_file = bundle_dir / "bundles" / bundle.name.removesuffix(".j2")
        smoke_file.parent.mkdir(parents=True, exist_ok=True)
        with smoke_file.open("w", encoding="utf-8") as content:
            content.write(bundle_template.render(context))
            print(f"Rendered smoke bundle: {smoke_file}")
        with smoke_file.open("r", encoding="utf-8") as content:
            print(content.read())
