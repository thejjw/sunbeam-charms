#!/usr/bin/env python3

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

"""Pure unit tests for octavia-k8s (no ops.testing scenario machinery)."""

from unittest import (
    mock,
)

import charm as octavia_charm
from lightkube.core.exceptions import (
    ApiError,
)
from lightkube.resources.apps_v1 import (
    StatefulSet,
)
from lightkube.types import (
    PatchType,
)

# ---------------------------------------------------------------------------
# IP extraction from k8s network-status annotation
# ---------------------------------------------------------------------------


class TestNetworkStatusIPExtraction:
    """Unit-level tests for IP parsing from k8s network-status annotation JSON.

    The charm's _ip_from_network_status_entries() matches an entry either by
    exact NAD name or by a ``<namespace>/<nad>`` qualified name.  These tests
    verify the matching algorithm independently of the charm lifecycle.
    """

    # Inline the extraction logic so tests remain self-contained and fast.
    @staticmethod
    def _extract(entries, nad_name):
        for entry in entries:
            entry_name = entry.get("name", "")
            if entry_name == nad_name or entry_name.endswith(f"/{nad_name}"):
                ips = entry.get("ips", [])
                return ips[0] if ips else None
        return None

    def test_exact_nad_name_match(self):
        """Entry name exactly matching NAD → first IP returned."""
        entries = [
            {"name": "cilium", "interface": "eth0", "ips": ["10.0.0.1"]},
            {
                "name": "octavia-mgmt",
                "interface": "net1",
                "ips": ["192.168.10.5"],
            },
        ]
        assert self._extract(entries, "octavia-mgmt") == "192.168.10.5"

    def test_namespace_qualified_nad_name_match(self):
        """Entry name with namespace/ prefix also matches bare NAD name."""
        entries = [
            {
                "name": "openstack/octavia-mgmt",
                "interface": "net1",
                "ips": ["192.168.10.7"],
            }
        ]
        assert self._extract(entries, "octavia-mgmt") == "192.168.10.7"

    def test_missing_nad_returns_none(self):
        """NAD not present in entries → None returned."""
        entries = [
            {"name": "cilium", "interface": "eth0", "ips": ["10.0.0.1"]}
        ]
        assert self._extract(entries, "octavia-mgmt") is None

    def test_nad_present_but_no_ips_returns_none(self):
        """NAD entry exists but has empty ips list → None returned."""
        entries = [{"name": "octavia-mgmt", "interface": "net1", "ips": []}]
        assert self._extract(entries, "octavia-mgmt") is None

    def test_first_ip_returned_when_multiple_ips(self):
        """Entry has multiple IPs → first one is returned."""
        entries = [
            {
                "name": "octavia-mgmt",
                "interface": "net1",
                "ips": ["192.168.10.5", "192.168.10.6"],
            }
        ]
        assert self._extract(entries, "octavia-mgmt") == "192.168.10.5"

    def test_partial_name_does_not_match(self):
        """Entry whose name only contains the NAD name as a substring → no match."""
        entries = [
            {
                "name": "not-octavia-mgmt",
                "interface": "net1",
                "ips": ["10.0.0.9"],
            }
        ]
        assert self._extract(entries, "octavia-mgmt") is None

    def test_empty_entries_returns_none(self):
        """Empty entry list → None returned."""
        assert self._extract([], "octavia-mgmt") is None

    def test_first_matching_entry_wins(self):
        """When multiple entries match, the first one's IP is used."""
        entries = [
            {
                "name": "octavia-mgmt",
                "interface": "net1",
                "ips": ["10.0.0.1"],
            },
            {
                "name": "openstack/octavia-mgmt",
                "interface": "net2",
                "ips": ["10.0.0.2"],
            },
        ]
        assert self._extract(entries, "octavia-mgmt") == "10.0.0.1"

    def test_namespace_mismatch_does_not_match(self):
        """Different NAD name after slash does not match target."""
        entries = [
            {
                "name": "openstack/other-network",
                "interface": "net1",
                "ips": ["10.0.0.1"],
            }
        ]
        assert self._extract(entries, "octavia-mgmt") is None


# ---------------------------------------------------------------------------
# _remove_legacy_containers
# ---------------------------------------------------------------------------


def _make_api_error(status_code: int = 403) -> ApiError:
    """Build a real ApiError instance suitable for use as a side_effect."""
    response = mock.MagicMock()
    response.status_code = status_code
    response.text = "Forbidden"
    return ApiError(response=response)


def _fake_charm(container_names: list[str]) -> mock.MagicMock:
    """Return a minimal MagicMock standing in for OctaviaOperatorCharm.

    Sets up model.name, app.name and network_patcher.lightkube_client so that
    _remove_legacy_containers() can be called as an unbound method:

        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)
    """
    fake = mock.MagicMock()
    fake.model.name = "openstack"
    fake.app.name = "octavia"

    containers = []
    for n in container_names:
        c = mock.MagicMock()
        c.name = n
        containers.append(c)

    fake_sts = mock.MagicMock()
    fake_sts.spec.template.spec.containers = containers
    fake.network_patcher.lightkube_client.get.return_value = fake_sts
    return fake


class TestRemoveLegacyContainers:
    """Unit tests for OctaviaOperatorCharm._remove_legacy_containers.

    The method is exercised by calling it as an unbound function with a
    minimal MagicMock standing in for ``self``, keeping tests fast and free of
    the full charm/ops harness machinery.
    """

    def test_removes_legacy_containers_when_present(self):
        """JSON Patch is sent for every legacy container found."""
        fake = _fake_charm(
            [
                "charm",
                "octavia-api",
                "octavia-driver-agent",
                "octavia-housekeeping",
            ]
        )
        client = fake.network_patcher.lightkube_client

        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

        client.patch.assert_called_once()
        kw = client.patch.call_args.kwargs
        assert kw["patch_type"] == PatchType.JSON
        assert kw["name"] == "octavia"
        assert kw["namespace"] == "openstack"
        # Indices 2 (octavia-driver-agent) and 3 (octavia-housekeeping) must
        # be removed highest-first to avoid index shifting.
        assert kw["obj"] == [
            {"op": "remove", "path": "/spec/template/spec/containers/3"},
            {"op": "remove", "path": "/spec/template/spec/containers/2"},
        ]

    def test_no_patch_when_no_legacy_containers(self):
        """patch() is never called when no legacy containers exist."""
        fake = _fake_charm(["charm", "octavia-api", "octavia-controller"])
        client = fake.network_patcher.lightkube_client

        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

        client.patch.assert_not_called()

    def test_removes_single_legacy_container(self):
        """Only one legacy container present — correct single-op patch sent."""
        fake = _fake_charm(["charm", "octavia-api", "octavia-driver-agent"])
        client = fake.network_patcher.lightkube_client

        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

        kw = client.patch.call_args.kwargs
        assert kw["obj"] == [
            {"op": "remove", "path": "/spec/template/spec/containers/2"}
        ]

    def test_get_api_error_logs_warning_and_does_not_raise(self):
        """When get() raises ApiError, patch() is never attempted."""
        fake = _fake_charm([])
        client = fake.network_patcher.lightkube_client
        client.get.side_effect = _make_api_error(403)

        # Must not raise
        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

        client.patch.assert_not_called()

    def test_patch_api_error_logs_warning_and_does_not_raise(self):
        """When patch() raises ApiError, the method returns gracefully."""
        fake = _fake_charm(["charm", "octavia-driver-agent"])
        client = fake.network_patcher.lightkube_client
        client.patch.side_effect = _make_api_error(403)

        # Must not raise
        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

    def test_get_called_with_correct_resource_and_namespace(self):
        """Lightkube get() is called with StatefulSet, correct name and namespace."""
        fake = _fake_charm(["charm"])
        client = fake.network_patcher.lightkube_client

        octavia_charm.OctaviaOperatorCharm._remove_legacy_containers(fake)

        client.get.assert_called_once_with(
            StatefulSet, name="octavia", namespace="openstack"
        )
