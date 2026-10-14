# Copyright 2026 Canonical Ltd.
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
"""Recover a database from an interrupted initial DB sync.

MySQL cannot roll back DDL, so an interrupted initial migration leaves tables
behind without recording a migration version, and the next sync fails as it
starts from scratch again. Such a schema has never been used by the service
and holds no data, so its tables are dropped to let the sync start cleanly.
The database and its grants are left in place.

This module is run as a script in the workload container, which has access
to the database and to sqlalchemy. It must not import anything else that is
unavailable there, including the rest of ops_sunbeam.
"""

import os

VERSION_TABLE = "alembic_version"
LEGACY_VERSION_TABLE = "migrate_version"


def is_partial_schema(tables: list[str], version_recorded: bool) -> bool:
    """Whether tables are left over from an interrupted initial DB sync.

    :param tables: names of the tables in the database
    :param version_recorded: whether the alembic version table holds a
                             migration version
    """
    if not tables:
        return False
    if LEGACY_VERSION_TABLE in tables:
        return False
    return not version_recorded


def recover_partial_schema(connection: str) -> int:
    """Drop the tables of a partially created schema.

    :param connection: sqlalchemy connection url of the database
    :return: number of tables dropped
    """
    # sqlalchemy is provided by the workload container, not by the charm.
    import sqlalchemy as sa  # type: ignore[import-not-found]

    engine = sa.create_engine(connection)
    with engine.connect() as conn:
        tables = sa.inspect(conn).get_table_names()
        version_recorded = VERSION_TABLE in tables and bool(
            conn.execute(
                sa.text(f"SELECT COUNT(*) FROM `{VERSION_TABLE}`")
            ).scalar()
        )
        if not is_partial_schema(tables, version_recorded):
            return 0
        conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
        for table in tables:
            conn.execute(sa.text(f"DROP TABLE IF EXISTS `{table}`"))
        conn.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))
    return len(tables)


def main() -> None:
    """Recover the database given in the DB_CONNECTION environment variable.

    Nothing is printed when the database is left untouched.
    """
    dropped = recover_partial_schema(os.environ["DB_CONNECTION"])
    if dropped:
        print(f"dropped {dropped} tables")


if __name__ == "__main__":
    main()
