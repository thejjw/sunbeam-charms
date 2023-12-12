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

# TODO: implement diff_tempest_config(old_filename: str, new_filename: str) -> str;


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
