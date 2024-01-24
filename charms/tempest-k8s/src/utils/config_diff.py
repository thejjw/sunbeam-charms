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

TODO: docs and implementation.
"""
from argparse import (
    ArgumentParser,
)
from configparser import (
    ConfigParser,
)
from typing import (
    Any,
)


def parse_config(f: str) -> ConfigParser:
    """Parse an INI config file."""
    # Anything in ConfigParser's default_section
    # will also appear in all other sections when reading.
    # Oslo doesn't use this default_section style of logic,
    # so we should hackily disable it.
    config = ConfigParser(
        interpolation=None, default_section="INTERNAL_ARBITRARY_UNUSED_SECTION"
    )
    config.read(f)
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


def diff_tempest_conf(filename_old: str, filename_new: str) -> str:
    """Report on changes between two tempest.conf files."""
    old = parse_config(filename_old)
    new = parse_config(filename_new)

    msgs = []

    for section in sorted(old):
        old_section = old[section]
        new_section = new.get(section, {})

        # check for keys in the section that were removed
        for key in old_section:
            if key not in new_section:
                value = censored(key, old_section[key])
                msgs.append(f"- [{section}] {key} = {value!r}")
                continue

            # check for keys that have different values
            if new_section[key] != old_section[key]:
                old = censored(key, old_section[key])
                new = censored(key, new_section[key])
                msgs.append(f"~ [{section}] {key} = {old!r} -> {new!r}")

    # check for sections and values that have been added
    for section in sorted(new):
        new_section = new[section]
        old_section = old.get(section, {})

        for key in new_section:
            if key not in old_section:
                value = censored(key, new_section[key])
                msgs.append(f"+ [{section}] {key} = {value!r}")

    return "\n".join(msgs)



def main():
    """Entry point of script if executed."""
    parser = ArgumentParser(
        prog="config_diff", description="tempest.conf smart diffing tool"
    )
    parser.add_argument("old", help="path to old config file")
    parser.add_argument("new", help="path to new config file")

    _ = parser.parse_args()

    # diff = diff_tempest_conf(args.old, args.new)
    # print(diff)
    print("TODO: implement comparison logic")


if __name__ == "__main__":
    main()
