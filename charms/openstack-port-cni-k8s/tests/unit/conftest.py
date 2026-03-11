# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared fixtures for openstack-port-cni-k8s unit tests.

When run through the repo tooling (``run_tox.sh``), ``repository.py prepare``
copies all required libs into ``lib/`` and sets ``PYTHONPATH=./src:./lib``.
When run standalone (e.g. ``uv run pytest``), this conftest adds the monorepo
source trees as fallback paths so tests can run without a prior prepare step.
"""

import sys
import unittest.mock as mock
from pathlib import (
    Path,
)

# ---------------------------------------------------------------------------
# Path setup — src/ + lib/ (prepared) + monorepo fallbacks (standalone).
# ---------------------------------------------------------------------------
_CHARM_ROOT = Path(__file__).parents[2]
_REPO_ROOT = _CHARM_ROOT.parent.parent

for _p in (
    _CHARM_ROOT / "src",
    _CHARM_ROOT / "lib",
    # Monorepo fallbacks when repository.py prepare has not been run:
    _REPO_ROOT / "ops-sunbeam",
    _REPO_ROOT / "charms" / "keystone-k8s" / "lib",
    _REPO_ROOT / "libs" / "external" / "lib",
):
    _ps = str(_p)
    if _p.exists() and _ps not in sys.path:
        sys.path.insert(0, _ps)

# These imports must come AFTER sys.path is configured.
import charm  # noqa: E402
import pytest  # noqa: E402
from ops import testing  # noqa: E402
from ops_sunbeam.test_utils_scenario import (  # noqa: E402
    identity_credentials_relation_complete,
    identity_credentials_secret,
    mandatory_relations_from_charmcraft,
    peer_relation,
)

CHARM_ROOT = _CHARM_ROOT
MANDATORY_RELATIONS = mandatory_relations_from_charmcraft(CHARM_ROOT)


# ---------------------------------------------------------------------------
# Mock fixtures — prevent real k8s / manifest operations in every test.
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_k8s():
    """A MagicMock representing a lightkube Client."""
    return mock.MagicMock()


@pytest.fixture(autouse=True)
def _patch_k8s(mock_k8s):
    """Prevent lightkube from connecting to a real cluster."""
    with mock.patch(
        "charm.OpenstackPortCniCharm._k8s_client", return_value=mock_k8s
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_manifests():
    """Prevent ops.manifests from making real k8s API calls."""
    with (
        mock.patch("ops.manifests.manifest.Manifests.apply_manifests"),
        mock.patch("ops.manifests.manifest.Manifests.delete_manifests"),
        mock.patch(
            "ops.manifests.collector.Collector.unready",
            new_callable=mock.PropertyMock,
            return_value=[],
        ),
        mock.patch(
            "ops.manifests.collector.Collector.short_version",
            new_callable=mock.PropertyMock,
            return_value="v0.39.0,0.1.0",
        ),
        mock.patch(
            "ops.manifests.collector.Collector.long_version",
            new_callable=mock.PropertyMock,
            return_value="ovs-cni:v0.39.0,openstack-port-cni:0.1.0",
        ),
        mock.patch(
            "charm.OpenstackPortCniCharm._daemonset_waiting",
            return_value="",
        ),
    ):
        yield


# ---------------------------------------------------------------------------
# State fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ctx():
    """testing.Context for OpenstackPortCniCharm."""
    return testing.Context(charm.OpenstackPortCniCharm, charm_root=CHARM_ROOT)


@pytest.fixture
def complete_relations():
    """All relations needed for the charm to reach active status."""
    return [
        identity_credentials_relation_complete(),
        peer_relation(),
    ]


@pytest.fixture
def complete_secrets():
    """Secrets required by the complete relations."""
    return [identity_credentials_secret()]


@pytest.fixture
def complete_state(complete_relations, complete_secrets):
    """Full state with leader=True, all mandatory relations and secrets."""
    return testing.State(
        leader=True,
        relations=complete_relations,
        secrets=complete_secrets,
    )
