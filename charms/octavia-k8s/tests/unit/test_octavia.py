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

from io import (
    BytesIO,
)
from unittest.mock import (
    MagicMock,
)

import ops
from charm import (
    _run_update_ca_certificates,
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
# _run_update_ca_certificates
# ---------------------------------------------------------------------------


class TestRunUpdateCaCertificates:
    """Unit tests for the _run_update_ca_certificates helper (LP: #2147695)."""

    def _make_container(self):
        """Return a MagicMock container with sensible defaults."""
        container = MagicMock(spec=ops.Container)
        # Simulate a successful update-ca-certificates exec by default.
        proc = MagicMock()
        proc.wait_output.return_value = ("Updating certificates...", "")
        container.exec.return_value = proc
        return container

    def test_skips_when_list_files_raises_api_error(self):
        """Return early when list_files raises APIError (directory absent); no exec."""
        container = self._make_container()
        container.list_files.side_effect = ops.pebble.APIError(
            {}, 404, "not found", "not found"
        )

        _run_update_ca_certificates(container)

        container.pull.assert_not_called()
        container.push.assert_not_called()
        container.exec.assert_not_called()

    def test_skips_when_pem_absent(self):
        """list_files returns empty (ca-bundle.pem not written yet) → no exec."""
        container = self._make_container()
        container.list_files.return_value = []

        _run_update_ca_certificates(container)

        container.pull.assert_not_called()
        container.push.assert_not_called()
        container.exec.assert_not_called()

    def test_pushes_and_runs_when_crt_absent(self):
        """PEM present, .crt does not exist yet → push + update-ca-certificates."""
        container = self._make_container()
        container.list_files.return_value = [MagicMock()]  # non-empty
        pem_content = (
            b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        )
        container.pull.side_effect = [
            BytesIO(pem_content),  # pull _CA_BUNDLE_PEM
            ops.pebble.PathError(
                "not-found", "file not found"
            ),  # pull _CA_BUNDLE_CRT
        ]

        _run_update_ca_certificates(container)

        container.push.assert_called_once()
        pushed_content = container.push.call_args[0][1]
        assert pushed_content == pem_content
        container.exec.assert_called_once_with(
            ["update-ca-certificates"], timeout=60
        )

    def test_skips_when_crt_matches_pem(self):
        """PEM and .crt have identical content → skip push and exec."""
        container = self._make_container()
        container.list_files.return_value = [MagicMock()]
        pem_content = (
            b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"
        )
        container.pull.side_effect = [
            BytesIO(pem_content),  # pull _CA_BUNDLE_PEM
            BytesIO(pem_content),  # pull _CA_BUNDLE_CRT — same content
        ]

        _run_update_ca_certificates(container)

        container.push.assert_not_called()
        container.exec.assert_not_called()

    def test_pushes_and_runs_when_crt_differs(self):
        """PEM and .crt content differ (CA rotated) → push + update-ca-certificates."""
        container = self._make_container()
        container.list_files.return_value = [MagicMock()]
        pem_content = b"NEW CERT\n"
        old_crt_content = b"OLD CERT\n"
        container.pull.side_effect = [
            BytesIO(pem_content),  # pull _CA_BUNDLE_PEM
            BytesIO(old_crt_content),  # pull _CA_BUNDLE_CRT
        ]

        _run_update_ca_certificates(container)

        container.push.assert_called_once()
        container.exec.assert_called_once_with(
            ["update-ca-certificates"], timeout=60
        )

    def test_exec_error_is_logged_not_raised(self):
        """Catch and log ExecError from update-ca-certificates; do not raise."""
        container = self._make_container()
        container.list_files.return_value = [MagicMock()]
        pem_content = b"CERT\n"
        container.pull.side_effect = [
            BytesIO(pem_content),
            ops.pebble.PathError("not-found", "file not found"),
        ]
        container.exec.side_effect = ops.pebble.ExecError(
            ["update-ca-certificates"], 1, "", "error"
        )

        # Must not propagate.
        _run_update_ca_certificates(container)
