#!/usr/bin/env python3

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

"""Unit tests for tempest.conf diff tool."""
import unittest
from tempfile import (
    NamedTemporaryFile,
)
from textwrap import (
    dedent,
)

from utils.config_diff import (
    diff_tempest_conf,
    main,
    parse_config,
)


class Tests(unittest.TestCase):
    """Test tempest.conf differ tool."""

    def test_the_same(self):
        """Verify no results are reported if config is logically the same."""
        old = parse_config(
            dedent(
                """
            [DEFAULT]

            # comments
            log_rotate_interval_type = days
            max_logfile_count = 30
            [auth]
            admin_username = admin
            admin_password = mysecret
            """
            )
        )

        new = parse_config(
            dedent(
                """
            [auth]

            admin_password = mysecret
            admin_username = admin
            [DEFAULT]

            # comments sholudn't create a diff
            log_rotate_interval_type = days
            max_logfile_count = 30
            """
            )
        )

        self.assertEqual(diff_tempest_conf(old, new), "")

    def test_normal_value_changed(self):
        """Verify expected result is reported if a value has changed."""
        old = parse_config(
            dedent(
                """
                [DEFAULT]
                log_rotate_interval_type = days
                max_logfile_count = 30

                [auth]
                admin_username = admin
                admin_password = mysecret
                """
            )
        )

        new = parse_config(
            dedent(
                """
                [DEFAULT]
                log_rotate_interval_type = days
                max_logfile_count = 30

                [auth]
                admin_username = superadmin
                admin_password = mysecret
                """
            )
        )

        self.assertEqual(
            diff_tempest_conf(old, new),
            "~ [auth] admin_username = 'admin' -> 'superadmin'",
        )

    def test_sensitive_value_changed(self):
        """Verify expected result is reported if a sensitive value changed."""
        old = parse_config(
            dedent(
                """
                [DEFAULT]
                log_rotate_interval_type = days
                max_logfile_count = 30

                [auth]
                admin_username = admin
                admin_password = mysecret
                """
            )
        )

        new = parse_config(
            dedent(
                """
                [DEFAULT]
                log_rotate_interval_type = days
                max_logfile_count = 30

                [auth]
                admin_username = admin
                admin_password = securepassword
                """
            )
        )

        self.assertEqual(
            diff_tempest_conf(old, new),
            "~ [auth] admin_password = '#CENSORED#' -> '#CENSORED#'",
        )

    def test_multiple_edits(self):
        """Verify expected results are reported with multiple changes."""
        old = parse_config(
            dedent(
                """
                [DEFAULT]
                max_logfile_count = 30

                [auth]
                admin_username = admin
                admin_password = mysecret
                """
            )
        )

        new = parse_config(
            dedent(
                """
            [DEFAULT]
            log_rotate_interval_type = days
            max_logfile_count = 31

            [auth]
            admin_username = admin
            """
            )
        )

        self.assertEqual(
            diff_tempest_conf(old, new),
            dedent(
                """
                + [DEFAULT] log_rotate_interval_type = 'days'
                - [auth] admin_password = '#CENSORED#'
                ~ [DEFAULT] max_logfile_count = '30' -> '31'
                """
            ).strip(),
        )

    def test_main(self):
        """Verify expected results with the main script entrypoint."""
        with NamedTemporaryFile("w") as old_file, NamedTemporaryFile(
            "w"
        ) as new_file:
            old_file.write(
                dedent(
                    """
                    [DEFAULT]
                    log_rotate_interval_type = days
                    max_logfile_count = 30
                    """
                )
            )
            new_file.write(
                dedent(
                    """
                    [DEFAULT]
                    log_rotate_interval_type = days
                    max_logfile_count = 30

                    [auth]
                    admin_username = myadmin
                    admin_password = supersecretpass
                    """
                )
            )
            old_file.seek(0)
            new_file.seek(0)

            self.assertEqual(
                main([old_file.name, new_file.name]),
                dedent(
                    """
                    + [auth] admin_password = '#CENSORED#'
                    + [auth] admin_username = 'myadmin'
                    """
                ).strip(),
            )
