# Copyright 2025 Canonical Ltd.
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

"""Tests for EPA client."""

import json
import tempfile
import unittest
from pathlib import (
    Path,
)
from unittest import (
    mock,
)

import epa_client


class TestEPAClient(unittest.TestCase):
    """Tests for EPAClient class."""

    def test_is_available_socket_exists(self):
        """Test is_available returns True when socket exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "epa.sock"
            socket_path.touch()

            client = epa_client.EPAClient(str(socket_path))
            self.assertTrue(client.is_available())

    def test_is_available_socket_not_exists(self):
        """Test is_available returns False when socket does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = Path(tmpdir) / "epa.sock"

            client = epa_client.EPAClient(str(socket_path))
            self.assertFalse(client.is_available())

    def test_is_available_with_default_socket_path(self):
        """Test is_available with default socket path."""
        client = epa_client.EPAClient()
        # Default path likely doesn't exist in test environment
        with mock.patch("os.path.exists", return_value=True):
            self.assertTrue(client.is_available())

        with mock.patch("os.path.exists", return_value=False):
            self.assertFalse(client.is_available())

    def test_is_available_with_nonexistent_directory(self):
        """Test is_available when parent directory doesn't exist."""
        client = epa_client.EPAClient("/nonexistent/path/epa.sock")
        self.assertFalse(client.is_available())

    @mock.patch("socket.socket")
    def test_send_request_success(self, mock_socket_class):
        """Test _send_request with successful response."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {"cores_allocated": "1,2,3"}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        request = {"action": "allocate_cores"}
        result = client._send_request(request)

        self.assertEqual(result, response_data)
        mock_sock.connect.assert_called_once_with("/test/epa.sock")
        mock_sock.sendall.assert_called_once()

    @mock.patch("socket.socket")
    def test_send_request_connection_error(self, mock_socket_class):
        """Test _send_request raises EPAConnectionError on connection failure."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError(
            "Connection refused"
        )

        client = epa_client.EPAClient("/test/epa.sock")
        request = {"action": "allocate_cores"}

        with self.assertRaises(epa_client.EPAConnectionError) as context:
            client._send_request(request)

        self.assertIn(
            "Unable to connect to EPA service socket", str(context.exception)
        )

    @mock.patch("socket.socket")
    def test_send_request_error_response(self, mock_socket_class):
        """Test _send_request raises EPAError when response contains error."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        error_response = {"error": "Invalid request"}
        mock_sock.recv.return_value = json.dumps(error_response).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        request = {"action": "allocate_cores"}

        with self.assertRaises(epa_client.EPAError) as context:
            client._send_request(request)

        self.assertIn("EPA request failed", str(context.exception))
        self.assertIn("Invalid request", str(context.exception))

    def test_parse_epa_core_list_empty(self):
        """Test _parse_epa_core_list with empty string."""
        client = epa_client.EPAClient()
        result = client._parse_epa_core_list("")
        self.assertEqual(result, [])

    def test_parse_epa_core_list_simple(self):
        """Test _parse_epa_core_list with comma-separated cores."""
        client = epa_client.EPAClient()
        result = client._parse_epa_core_list("1,2,3")
        self.assertEqual(result, [1, 2, 3])

    def test_parse_epa_core_list_ranges(self):
        """Test _parse_epa_core_list with ranges."""
        client = epa_client.EPAClient()
        result = client._parse_epa_core_list("1-4,9-12")
        self.assertEqual(result, [1, 2, 3, 4, 9, 10, 11, 12])

    def test_parse_epa_core_list_mixed(self):
        """Test _parse_epa_core_list with mixed format."""
        client = epa_client.EPAClient()
        result = client._parse_epa_core_list("1,3-5,7")
        self.assertEqual(result, [1, 3, 4, 5, 7])

    @mock.patch("socket.socket")
    def test_allocate_cores_without_numa(self, mock_socket_class):
        """Test allocate_cores without NUMA node specification."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {"cores_allocated": "0,1,2,3"}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        result = client.allocate_cores("test-service", 4)

        self.assertEqual(result, [0, 1, 2, 3])

        # Verify request format
        call_args = mock_sock.sendall.call_args[0][0]
        request = json.loads(call_args.decode())
        self.assertEqual(request["action"], "allocate_cores")
        self.assertEqual(request["service_name"], "test-service")
        self.assertEqual(request["num_of_cores"], 4)
        self.assertNotIn("numa_node", request)

    @mock.patch("socket.socket")
    def test_allocate_cores_with_numa(self, mock_socket_class):
        """Test allocate_cores with NUMA node specification."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {"cores_allocated": "0-3"}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        result = client.allocate_cores("test-service", 4, numa_node=1)

        self.assertEqual(result, [0, 1, 2, 3])

        # Verify request format
        call_args = mock_sock.sendall.call_args[0][0]
        request = json.loads(call_args.decode())
        self.assertEqual(request["action"], "allocate_numa_cores")
        self.assertEqual(request["numa_node"], 1)

    @mock.patch("socket.socket")
    def test_allocate_cores_deallocate(self, mock_socket_class):
        """Test allocate_cores with -1 to deallocate."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {"cores_allocated": ""}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        result = client.allocate_cores("test-service", -1, numa_node=0)

        self.assertEqual(result, [])

    @mock.patch("socket.socket")
    def test_allocate_hugepages(self, mock_socket_class):
        """Test allocate_hugepages."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        client.allocate_hugepages(
            service_name="test-service",
            hugepages_requested=4,
            hugepage_size_kb=1048576,
            numa_node=0,
        )

        # Verify request format
        call_args = mock_sock.sendall.call_args[0][0]
        request = json.loads(call_args.decode())
        self.assertEqual(request["action"], "allocate_hugepages")
        self.assertEqual(request["service_name"], "test-service")
        self.assertEqual(request["hugepages_requested"], 4)
        self.assertEqual(request["size_kb"], 1048576)
        self.assertEqual(request["node_id"], 0)

    @mock.patch("socket.socket")
    def test_allocate_hugepages_deallocate(self, mock_socket_class):
        """Test allocate_hugepages with -1 to deallocate."""
        mock_sock = mock.MagicMock()
        mock_socket_class.return_value.__enter__.return_value = mock_sock

        response_data = {}
        mock_sock.recv.return_value = json.dumps(response_data).encode()

        client = epa_client.EPAClient("/test/epa.sock")
        client.allocate_hugepages(
            service_name="test-service",
            hugepages_requested=-1,
            hugepage_size_kb=1048576,
            numa_node=0,
        )

        call_args = mock_sock.sendall.call_args[0][0]
        request = json.loads(call_args.decode())
        self.assertEqual(request["hugepages_requested"], -1)


class TestEPAExceptions(unittest.TestCase):
    """Tests for EPA exception classes."""

    def test_epa_error_inheritance(self):
        """Test EPAError is an Exception."""
        error = epa_client.EPAError("Test error")
        self.assertIsInstance(error, Exception)
        self.assertEqual(str(error), "Test error")

    def test_epa_connection_error_inheritance(self):
        """Test EPAConnectionError inherits from EPAError."""
        error = epa_client.EPAConnectionError("Connection failed")
        self.assertIsInstance(error, epa_client.EPAError)
        self.assertIsInstance(error, Exception)
        self.assertEqual(str(error), "Connection failed")
