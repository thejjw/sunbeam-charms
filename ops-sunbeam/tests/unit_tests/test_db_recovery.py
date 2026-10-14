# Copyright 2026 Canonical Ltd.
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

"""Test db recovery code."""

import sys
from unittest.mock import (
    MagicMock,
    patch,
)

sys.path.append("lib")  # noqa
sys.path.append("src")  # noqa

import ops_sunbeam.db_recovery as sunbeam_db_recovery
import ops_sunbeam.test_utils as test_utils


class TestDBRecovery(test_utils.CharmTestCase):
    """Test for the db_recovery module."""

    PATCHES = []

    def setUp(self) -> None:
        """Mock sqlalchemy, which is only available in the workload."""
        super().setUp(sunbeam_db_recovery, self.PATCHES)
        self.sa = MagicMock()
        self.sa.text.side_effect = lambda statement: statement
        self.conn = (
            self.sa.create_engine.return_value.connect.return_value.__enter__.return_value
        )
        patcher = patch.dict(sys.modules, {"sqlalchemy": self.sa})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_schema(self, tables: list[str], versions: int = 0) -> None:
        """Set the tables and recorded migration versions of the database."""
        self.sa.inspect.return_value.get_table_names.return_value = tables
        self.conn.execute.return_value.scalar.return_value = versions

    def _executed(self) -> list[str]:
        """Return the statements executed on the database."""
        return [call.args[0] for call in self.conn.execute.call_args_list]

    def test_is_partial_schema(self) -> None:
        """Test only tables without a migration version are partial."""
        is_partial = sunbeam_db_recovery.is_partial_schema
        self.assertFalse(is_partial([], False))
        self.assertFalse(is_partial(["alembic_version", "instances"], True))
        self.assertFalse(is_partial(["migrate_version", "instances"], False))
        self.assertTrue(is_partial(["alembic_version", "instances"], False))
        self.assertTrue(is_partial(["instances"], False))

    def test_recover_empty_database(self) -> None:
        """Test an empty database is left untouched."""
        self._set_schema([])
        self.assertEqual(
            sunbeam_db_recovery.recover_partial_schema("mysql://db"), 0
        )
        self.sa.create_engine.assert_called_once_with("mysql://db")
        self.assertEqual(self._executed(), [])

    def test_recover_initialised_database(self) -> None:
        """Test a database with a migration version is left untouched."""
        self._set_schema(["alembic_version", "instances"], versions=1)
        self.assertEqual(
            sunbeam_db_recovery.recover_partial_schema("mysql://db"), 0
        )
        self.assertEqual(
            self._executed(), ["SELECT COUNT(*) FROM `alembic_version`"]
        )

    def test_recover_legacy_database(self) -> None:
        """Test a database tracked by sqlalchemy-migrate is left untouched."""
        self._set_schema(["migrate_version", "instances"])
        self.assertEqual(
            sunbeam_db_recovery.recover_partial_schema("mysql://db"), 0
        )
        self.assertEqual(self._executed(), [])

    def test_recover_partial_schema(self) -> None:
        """Test tables without a migration version are dropped."""
        self._set_schema(["alembic_version", "instances", "quotas"])
        self.assertEqual(
            sunbeam_db_recovery.recover_partial_schema("mysql://db"), 3
        )
        self.assertEqual(
            self._executed(),
            [
                "SELECT COUNT(*) FROM `alembic_version`",
                "SET FOREIGN_KEY_CHECKS=0",
                "DROP TABLE IF EXISTS `alembic_version`",
                "DROP TABLE IF EXISTS `instances`",
                "DROP TABLE IF EXISTS `quotas`",
                "SET FOREIGN_KEY_CHECKS=1",
            ],
        )

    def test_recover_schema_without_version_table(self) -> None:
        """Test tables are dropped when the version table is missing."""
        self._set_schema(["instances"])
        self.assertEqual(
            sunbeam_db_recovery.recover_partial_schema("mysql://db"), 1
        )
        self.assertEqual(
            self._executed(),
            [
                "SET FOREIGN_KEY_CHECKS=0",
                "DROP TABLE IF EXISTS `instances`",
                "SET FOREIGN_KEY_CHECKS=1",
            ],
        )

    def test_main_reports_dropped_tables(self) -> None:
        """Test main only prints when tables were dropped."""
        self._set_schema(["instances"])
        with patch.dict("os.environ", {"DB_CONNECTION": "mysql://db"}):
            with patch("builtins.print") as mock_print:
                sunbeam_db_recovery.main()
                mock_print.assert_called_once_with("dropped 1 tables")
                mock_print.reset_mock()
                self._set_schema([])
                sunbeam_db_recovery.main()
                mock_print.assert_not_called()
