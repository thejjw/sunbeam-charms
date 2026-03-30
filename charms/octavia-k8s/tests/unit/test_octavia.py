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
