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

"""Unit tests for the ceph_access lib (requires side), multi-relation."""

import ops
import pytest
from charms.cinder_volume_ceph.v0 import (
    ceph_access,
)
from ops import (
    testing,
)

ENDPOINT = "ceph-access"
META = {
    "name": "ceph-access-requirer",
    "requires": {ENDPOINT: {"interface": "cinder-ceph-key"}},
}

UUID_A = "aaaaaaaa-0000-4000-8000-000000000001"
UUID_B = "bbbbbbbb-0000-4000-8000-000000000002"


class RequirerCharm(ops.CharmBase):
    """Minimal charm hosting CephAccessRequires."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        self.ceph_access = ceph_access.CephAccessRequires(self, ENDPOINT)
        self.ready_count = 0
        self.goneaway_count = 0
        framework.observe(self.ceph_access.on.ready, self._on_ready)
        framework.observe(self.ceph_access.on.goneaway, self._on_goneaway)

    def _on_ready(self, _):
        self.ready_count += 1

    def _on_goneaway(self, _):
        self.goneaway_count += 1


@pytest.fixture()
def ctx():
    return testing.Context(RequirerCharm, meta=META)


def access(app: str, uuid: str, key: str, with_secret: bool = True):
    """Return (relation, secret) for one provider app."""
    secret = testing.Secret(tracked_content={"uuid": uuid, "key": key})
    rel = testing.Relation(
        endpoint=ENDPOINT,
        remote_app_name=app,
        remote_app_data=(
            {"access-credentials": secret.id} if with_secret else {}
        ),
    )
    return rel, secret


# --- Baseline: single relation (must pass before and after LIBPATCH 2) ---


def test_single_relation_legacy_properties(ctx):
    rel, sec = access("cinder-volume-ceph", UUID_A, "key-a")
    state = testing.State(relations=[rel], secrets=[sec])
    with ctx(ctx.on.relation_changed(rel), state) as mgr:
        mgr.run()
        lib = mgr.charm.ceph_access
        assert lib.ready
        assert lib.ceph_access_data == {"uuid": UUID_A, "key": "key-a"}
        assert mgr.charm.ready_count == 1


# --- Regression: two relations (fails on LIBPATCH 1) ---


def test_two_relations_changed_does_not_raise(ctx):
    """LIBPATCH 1 raises TooManyRelatedAppsError -> UncaughtCharmError."""
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, sec_b = access("ceph-ext", UUID_B, "key-b")
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a, sec_b])
    with ctx(ctx.on.relation_changed(rel_b), state) as mgr:
        mgr.run()
        assert mgr.charm.ready_count == 1


def test_data_by_app_returns_every_provider(ctx):
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, sec_b = access("ceph-ext", UUID_B, "key-b")
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a, sec_b])
    with ctx(ctx.on.relation_changed(rel_a), state) as mgr:
        mgr.run()
        assert mgr.charm.ceph_access.ceph_access_data_by_app == {
            "ceph-embedded": {"uuid": UUID_A, "key": "key-a"},
            "ceph-ext": {"uuid": UUID_B, "key": "key-b"},
        }


def test_data_by_app_skips_incomplete_relation(ctx):
    """A relation without access-credentials yet is omitted, not an error."""
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, _ = access("ceph-ext", UUID_B, "key-b", with_secret=False)
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a])
    with ctx(ctx.on.relation_changed(rel_b), state) as mgr:
        mgr.run()
        lib = mgr.charm.ceph_access
        assert lib.ceph_access_data_by_app == {
            "ceph-embedded": {"uuid": UUID_A, "key": "key-a"},
        }
        assert lib.ready  # at least one relation ready


def test_data_by_app_skips_missing_secret(ctx):
    """Secret id published but not visible -> omitted, not an error."""
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, _unseen = access("ceph-ext", UUID_B, "key-b")
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a])
    with ctx(ctx.on.relation_changed(rel_a), state) as mgr:
        mgr.run()
        assert set(mgr.charm.ceph_access.ceph_access_data_by_app) == {
            "ceph-embedded"
        }


def test_legacy_properties_with_two_relations_use_oldest(ctx):
    """Legacy single-slot properties stay usable: oldest (lowest id) wins."""
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, sec_b = access("ceph-ext", UUID_B, "key-b")
    assert rel_a.id < rel_b.id
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a, sec_b])
    with ctx(ctx.on.relation_changed(rel_b), state) as mgr:
        mgr.run()
        lib = mgr.charm.ceph_access
        assert lib.ready
        assert lib.ceph_access_data == {"uuid": UUID_A, "key": "key-a"}


def test_relation_broken_excludes_breaking_app(ctx):
    """ops excludes the breaking relation from model.relations (ops>=2.10)."""
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    rel_b, sec_b = access("ceph-ext", UUID_B, "key-b")
    state = testing.State(relations=[rel_a, rel_b], secrets=[sec_a, sec_b])
    with ctx(ctx.on.relation_broken(rel_b), state) as mgr:
        mgr.run()
        charm = mgr.charm
        assert charm.goneaway_count == 1
        assert charm.ceph_access.ceph_access_data_by_app == {
            "ceph-embedded": {"uuid": UUID_A, "key": "key-a"},
        }


def test_last_relation_broken_is_empty(ctx):
    rel_a, sec_a = access("ceph-embedded", UUID_A, "key-a")
    state = testing.State(relations=[rel_a], secrets=[sec_a])
    with ctx(ctx.on.relation_broken(rel_a), state) as mgr:
        mgr.run()
        lib = mgr.charm.ceph_access
        assert lib.ceph_access_data_by_app == {}
        assert not lib.ready
        assert lib.ceph_access_data == {}
