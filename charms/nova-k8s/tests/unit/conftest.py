# Copyright 2025 Canonical Ltd.
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

"""Shared fixtures for nova-k8s scenario tests."""

import pytest
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequiresEvents,
)


@pytest.fixture(autouse=True)
def _cleanup_database_requires_events():
    """Remove dynamically-defined events so the next Context can re-create them.

    Nova has 3 database relations (database, api-database, cell-database),
    so we must clean up events for all three prefixes.
    """
    yield
    for attr in list(vars(DatabaseRequiresEvents)):
        if attr.endswith(
            (
                "_database_created",
                "_endpoints_changed",
                "_read_only_endpoints_changed",
            )
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass
