# Copyright 2023 Canonical Ltd.
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
"""Utility for smart diffing tempest.conf for tempest-k8s charm.

Takes into account reordering of fields and censoring sensitive values.
"""
import sys
from argparse import (
    ArgumentParser,
)
from configparser import (
    ConfigParser,
)
from typing import (
    Any,
    List,
)


def parse_config(contents: str) -> ConfigParser:
    """Parse a string of INI config, with better compatibility with oslo.

    Raises errors from ConfigParser on parsing invalid config.
    """
    config = ConfigParser(
        # Oslo doesn't do interpolation
        interpolation=None,
        # Anything in ConfigParser's default_section
        # will also appear in all other sections when reading.
        # Oslo doesn't use this default_section style of logic,
        # so we should hackily disable it.
        default_section="INTERNAL_ARBITRARY_UNUSED_SECTION",
    )
    config.read_string(contents)
    return config


def censored(key: str, value: Any) -> Any:
    """Return the value or censor if it should be censored."""
    if key in [
        "admin_password",
        "image_ssh_password",
        "image_alt_ssh_password",
        "password",
        "alt_password",
    ]:
        return "CENSORED"
    return value


def diff_tempest_conf(old: ConfigParser, new: ConfigParser) -> str:
    """Report on changes between two tempest.conf configs."""
    msgs = []

    for section in old:
        old_section = old[section]
        new_section = new[section] if new.has_section(section) else {}

        # check for keys in the section that were removed
        for key in old_section:
            if key not in new_section:
                value = censored(key, old_section[key])
                msgs.append(f"- [{section}] {key} = {value!r}")
                continue

            # check for keys that have different values
            if new_section[key] != old_section[key]:
                old_value = censored(key, old_section[key])
                new_value = censored(key, new_section[key])
                msgs.append(
                    f"~ [{section}] {key} = {old_value!r} -> {new_value!r}"
                )

    # check for sections and values that have been added
    for section in new:
        new_section = new[section]
        old_section = old[section] if old.has_section(section) else {}

        for key in new_section:
            if key not in old_section:
                value = censored(key, new_section[key])
                msgs.append(f"+ [{section}] {key} = {value!r}")

    return "\n".join(sorted(msgs))

def main(args: List[str]) -> str:
    """Entry point of script when executed directly.

    Takes command line arguments,
    and returns the output.
    """
    parser = ArgumentParser(
        prog="config_diff", description="tempest.conf smart diffing tool"
    )
    parser.add_argument("old", help="path to old config file")
    parser.add_argument("new", help="path to new config file")

    parsed_args = parser.parse_args(args)

    with open(parsed_args.old) as f:
        old = parse_config(f.read())
    with open(parsed_args.new) as f:
        new = parse_config(f.read())

    return diff_tempest_conf(old, new)


if __name__ == "__main__":
    print(main(sys.argv[1:]))
