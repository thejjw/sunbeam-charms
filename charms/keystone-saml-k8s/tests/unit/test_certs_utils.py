#!/usr/bin/env python3

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

"""Test certificate utilities."""

import unittest

import certs
import ops_sunbeam.test_utils as test_utils


class TestCertificateUtils(unittest.TestCase):
    """Test certificate utility functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Use the project's standard test certificate
        self.valid_cert_pem = test_utils.TEST_CA

    def test_parse_cert_chain_empty(self):
        """Test parsing empty certificate chain."""
        result = certs.parse_cert_chain("")
        self.assertEqual(result, [])

    def test_parse_cert_chain_valid(self):
        """Test parsing valid certificate chain."""
        result = certs.parse_cert_chain(self.valid_cert_pem)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], self.valid_cert_pem)

    def test_parse_cert_chain_invalid(self):
        """Test parsing invalid certificate."""
        invalid_pem = """-----BEGIN CERTIFICATE-----
INVALID_DATA
-----END CERTIFICATE-----"""
        with self.assertRaises(ValueError) as context:
            certs.parse_cert_chain(invalid_pem)
        self.assertIn(
            "Certificate #1 is corrupted or invalid", str(context.exception)
        )

    def test_is_valid_chain_empty(self):
        """Test validation of empty chain."""
        # Empty chain should be invalid
        result = certs.is_valid_chain("")
        self.assertFalse(result)

    def test_is_valid_chain_valid(self):
        """Test validation of valid chain."""
        result = certs.is_valid_chain(self.valid_cert_pem)
        self.assertTrue(result)

    def test_is_valid_chain_invalid(self):
        """Test validation of invalid chain."""
        invalid_pem = """-----BEGIN CERTIFICATE-----
INVALID_DATA
-----END CERTIFICATE-----"""
        result = certs.is_valid_chain(invalid_pem)
        self.assertFalse(result)
