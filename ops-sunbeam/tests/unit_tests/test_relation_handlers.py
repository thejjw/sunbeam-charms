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

"""Test TestTlsCertificatesHandler for certificate renewals."""

from unittest.mock import (
    MagicMock,
    patch,
)

import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.test_utils as test_utils


class TestTlsCertificatesHandler(test_utils.CharmTestCase):
    """Test for the TlsCertificatesHandler class."""

    PATCHES = []

    def setUp(self) -> None:
        """Set up the test environment."""
        super().setUp(test_utils, self.PATCHES)

        self.mock_charm = MagicMock()

        # Patch both setup_event_handler and __post_init__ to avoid interface issues
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            self.handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                mandatory=True,
            )
        # Mock the interface attribute with proper methods
        self.handler.interface = MagicMock()
        self.handler.interface.get_assigned_certificates.return_value = (
            [],
            [],
        )
        self.handler.interface.get_provider_certificates.return_value = []
        self.handler.interface.private_key = "mock_private_key"

    def test_custom_certificate_requests(self) -> None:
        """Test that custom certificate requests are used when provided."""
        # Mock the CertificateRequestAttributes class
        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.CertificateRequestAttributes"
        ) as mock_cert_req, patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            mock_request = MagicMock()
            mock_request.common_name = "custom-service"
            mock_request.sans_dns = frozenset(["api.example.com"])
            mock_request.sans_ip = frozenset(["10.0.0.1"])
            mock_cert_req.return_value = mock_request

            custom_requests = [mock_request]

            handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                certificate_requests=custom_requests,
                mandatory=True,
            )

            self.assertEqual(handler.certificate_requests, custom_requests)
            self.assertEqual(len(handler.certificate_requests), 1)
            self.assertEqual(
                handler.certificate_requests[0].common_name, "custom-service"
            )

    def test_default_certificate_requests(self) -> None:
        """Test that default certificate requests are used when none provided."""
        # Mock the get_entity method and CertificateRequestAttributes
        mock_entity = MagicMock()
        mock_entity.name = "test/charm"

        with patch(
            "charms.tls_certificates_interface.v4.tls_certificates.CertificateRequestAttributes"
        ) as mock_cert_req, patch.object(
            self.handler, "get_entity", return_value=mock_entity
        ):

            mock_request = MagicMock()
            mock_request.common_name = "test-charm"
            mock_cert_req.return_value = mock_request

            requests = self.handler.default_certificate_requests()
            self.assertEqual(len(requests), 1)
            mock_cert_req.assert_called_once_with(
                common_name="test-charm",
                sans_dns=None,
                sans_ip=None,
            )

    def test_get_entity_app_managed(self) -> None:
        """Test get_entity when app_managed_certificates=True."""
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                app_managed_certificates=True,
                mandatory=True,
            )

        entity = handler.get_entity()
        self.assertEqual(entity, self.mock_charm.model.app)

    def test_get_entity_unit_managed(self) -> None:
        """Test get_entity when app_managed_certificates=False (default)."""
        entity = self.handler.get_entity()
        self.assertEqual(entity, self.mock_charm.model.unit)

    def test_context_single_certificate(self) -> None:
        """Test context method with single certificate."""
        mock_cert = MagicMock()
        mock_cert.ca = "ca_cert_content"
        mock_cert.chain = ["chain_cert_1", "chain_cert_2"]
        mock_cert.certificate = "cert_content"

        with patch.object(
            self.handler, "get_certs", return_value=[("test-charm", mock_cert)]
        ), patch.object(
            self.handler, "get_private_key", return_value="private_key"
        ):

            context = self.handler.context()

            expected = {
                "key": "private_key",
                "ca_cert": "ca_cert_content",
                "ca_with_chain": "ca_cert_content\nchain_cert_1\nchain_cert_2",
                "cert": "cert_content",
            }
            self.assertEqual(context, expected)

    def test_context_multiple_certificates(self) -> None:
        """Test context method with multiple certificates."""
        mock_cert1 = MagicMock()
        mock_cert1.ca = "ca_cert_1"
        mock_cert1.chain = ["chain_1_1"]
        mock_cert1.certificate = "cert_1"

        mock_cert2 = MagicMock()
        mock_cert2.ca = "ca_cert_2"
        mock_cert2.chain = ["chain_2_1"]
        mock_cert2.certificate = "cert_2"

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("cert1", mock_cert1), ("cert2", mock_cert2)],
        ), patch.object(
            self.handler, "get_private_key", return_value="private_key"
        ):

            context = self.handler.context()

            # With the improved context method, both certificates should be present
            expected = {
                "key": "private_key",
                "ca_cert": "ca_cert_1",  # First cert's ca
                "ca_with_chain": "ca_cert_1\nchain_1_1",  # First cert's chain
                "cert": "cert_1",  # First cert
                "key_cert2": "private_key",  # Second cert with suffix
                "ca_cert_cert2": "ca_cert_2",  # Second cert's ca
                "ca_with_chain_cert2": "ca_cert_2\nchain_2_1",  # Second cert's chain
                "cert_cert2": "cert_2",  # Second cert
            }
            self.assertEqual(context, expected)

    def test_context_no_certificates(self) -> None:
        """Test context method when no certificates are available."""
        with patch.object(self.handler, "get_certs", return_value=[]):
            context = self.handler.context()
            self.assertEqual(context, {})

    def test_context_none_certificates(self) -> None:
        """Test context method when certificates are None."""
        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("cert1", None), ("cert2", None)],
        ), patch.object(
            self.handler, "get_private_key", return_value="private_key"
        ):
            context = self.handler.context()
            self.assertEqual(context, {})

    def test_ready_with_certificates(self) -> None:
        """Test ready property when certificates are available."""
        mock_cert = MagicMock()
        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("test.example.com", mock_cert)],
        ):
            self.assertTrue(self.handler.ready)

    def test_ready_without_certificates(self) -> None:
        """Test ready property when no certificates are available."""
        with patch.object(self.handler, "get_certs", return_value=[]):
            self.assertFalse(self.handler.ready)

    def test_ready_with_none_certificates(self) -> None:
        """Test ready property when certificates are None."""
        with patch.object(
            self.handler, "get_certs", return_value=[("cert1", None)]
        ):
            self.assertFalse(self.handler.ready)

    def test_ready_with_mixed_certificates(self) -> None:
        """Test ready property with mix of None and valid certificates."""
        mock_cert = MagicMock()
        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("cert1", None), ("cert2", mock_cert)],
        ):
            # Should return True because at least one certificate is not None
            self.assertTrue(self.handler.ready)

    def test_ready_app_managed_non_leader(self) -> None:
        """Test ready property for app-managed certs on non-leader unit."""
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                app_managed_certificates=True,
                mandatory=True,
            )
            handler.interface = MagicMock()
            handler.interface.get_assigned_certificates.return_value = ([], [])
            handler.interface.get_provider_certificates.return_value = []
            handler.interface.private_key = "mock_private_key"

        # Mock non-leader unit
        handler.model.unit.is_leader.return_value = False

        with patch.object(
            handler,
            "get_certs",
            return_value=[("cert1", None)],
        ):
            # Should return True for non-leader unit with app-managed certs
            # even though certificates are None
            self.assertTrue(handler.ready)

    def test_ready_app_managed_leader_with_none(self) -> None:
        """Test ready property for app-managed certs on leader unit with None."""
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                app_managed_certificates=True,
                mandatory=True,
            )
            handler.interface = MagicMock()
            handler.interface.get_assigned_certificates.return_value = ([], [])
            handler.interface.get_provider_certificates.return_value = []
            handler.interface.private_key = "mock_private_key"

        # Mock leader unit
        handler.model.unit.is_leader.return_value = True

        with patch.object(
            handler,
            "get_certs",
            return_value=[("cert1", None)],
        ):
            # Should return False for leader unit even with app-managed certs
            # because certificate is None
            self.assertFalse(handler.ready)

    def test_ready_app_managed_leader_with_valid_cert(self) -> None:
        """Test ready property for app-managed certs on leader unit with valid cert."""
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "__post_init__",
            return_value=None,
        ):
            handler = sunbeam_rhandlers.TlsCertificatesHandler(
                charm=self.mock_charm,
                relation_name="certificates",
                callback_f=MagicMock(),
                app_managed_certificates=True,
                mandatory=True,
            )
            handler.interface = MagicMock()
            handler.interface.get_assigned_certificates.return_value = ([], [])
            handler.interface.get_provider_certificates.return_value = []
            handler.interface.private_key = "mock_private_key"

        # Mock leader unit
        handler.model.unit.is_leader.return_value = True

        mock_cert = MagicMock()
        with patch.object(
            handler,
            "get_certs",
            return_value=[("cert1", mock_cert)],
        ):
            # Should return True for leader unit with valid certificate
            self.assertTrue(handler.ready)

    def test_ready_unit_managed_with_multiple_certs(self) -> None:
        """Test ready property for unit-managed certs with multiple certificates."""
        mock_cert1 = MagicMock()
        mock_cert2 = MagicMock()

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[
                ("cert1", mock_cert1),
                ("cert2", mock_cert2),
            ],
        ):
            # Should return True when at least one certificate is valid
            self.assertTrue(self.handler.ready)

    def test_get_certificate_context_found(self) -> None:
        """Test get_certificate_context method when certificate is found."""
        mock_cert = MagicMock()
        mock_cert.ca = "ca_cert_content"
        mock_cert.chain = ["chain_cert_1", "chain_cert_2"]
        mock_cert.certificate = "cert_content"

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("test-service", mock_cert)],
        ), patch.object(
            self.handler, "get_private_key_secret", return_value="secret:12345"
        ):

            result = self.handler.get_certificate_context("test-service")

            expected = {
                "key": "secret:12345",
                "ca_cert": "ca_cert_content",
                "ca_with_chain": "ca_cert_content\nchain_cert_1\nchain_cert_2",
                "cert": "cert_content",
            }
            self.assertEqual(result, expected)

    def test_get_certificate_context_not_found(self) -> None:
        """Test get_certificate_context method when certificate is not found."""
        mock_cert = MagicMock()
        mock_cert.ca = "ca_cert_content"
        mock_cert.chain = ["chain_cert_1"]
        mock_cert.certificate = "cert_content"

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("other-service", mock_cert)],
        ):

            result = self.handler.get_certificate_context("test-service")
            self.assertEqual(result, {})

    def test_get_certificate_context_multiple_certificates(self) -> None:
        """Test get_certificate_context method with multiple certificates."""
        mock_cert1 = MagicMock()
        mock_cert1.ca = "ca_cert_1"
        mock_cert1.chain = ["chain_1"]
        mock_cert1.certificate = "cert_1"

        mock_cert2 = MagicMock()
        mock_cert2.ca = "ca_cert_2"
        mock_cert2.chain = ["chain_2_1", "chain_2_2"]
        mock_cert2.certificate = "cert_2"

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[
                ("service-1", mock_cert1),
                ("service-2", mock_cert2),
            ],
        ), patch.object(
            self.handler, "get_private_key_secret", return_value="secret:12345"
        ):

            # Test finding the second certificate
            result = self.handler.get_certificate_context("service-2")

            expected = {
                "key": "secret:12345",
                "ca_cert": "ca_cert_2",
                "ca_with_chain": "ca_cert_2\nchain_2_1\nchain_2_2",
                "cert": "cert_2",
            }
            self.assertEqual(result, expected)

    def test_get_certificate_context_empty_chain(self) -> None:
        """Test get_certificate_context method with empty certificate chain."""
        mock_cert = MagicMock()
        mock_cert.ca = "ca_cert_content"
        mock_cert.chain = []  # Empty chain
        mock_cert.certificate = "cert_content"

        with patch.object(
            self.handler,
            "get_certs",
            return_value=[("test-service", mock_cert)],
        ), patch.object(
            self.handler, "get_private_key_secret", return_value="secret:12345"
        ):

            result = self.handler.get_certificate_context("test-service")

            expected = {
                "key": "secret:12345",
                "ca_cert": "ca_cert_content",
                "ca_with_chain": "ca_cert_content",  # Only CA cert, no chain
                "cert": "cert_content",
            }
            self.assertEqual(result, expected)

    def test_get_certificate_context_no_certificates(self) -> None:
        """Test get_certificate_context method when no certificates exist."""
        with patch.object(self.handler, "get_certs", return_value=[]):

            result = self.handler.get_certificate_context("test-service")
            self.assertEqual(result, {})

    def test_get_certificate_context_none_certificate(self) -> None:
        """Test get_certificate_context method when certificate is None."""
        with patch.object(
            self.handler, "get_certs", return_value=[("test-service", None)]
        ):

            result = self.handler.get_certificate_context("test-service")
            self.assertEqual(result, {})

    def test_validate_and_regenerate_no_csrs_with_expected_requests(
        self,
    ) -> None:
        """Test validation when no CSRs exist but expected requests are provided."""
        # Mock expected certificate request
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = ["api.example.com"]
        mock_expected_request.sans_ip = ["10.0.0.1"]

        # Mock empty CSRs from relation
        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = (
            []
        )

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify certificates were regenerated
        self.assertEqual(
            self.handler.interface.certificate_requests,
            [mock_expected_request],
        )
        self.handler.interface.sync.assert_called_once()

    def test_validate_and_regenerate_sans_match(self) -> None:
        """Test validation when SANs match - no regeneration needed."""
        # Mock expected certificate request
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = ["api.example.com", "web.example.com"]
        mock_expected_request.sans_ip = ["10.0.0.1"]

        # Mock CSR with matching SANs
        mock_csr = MagicMock()
        mock_csr.common_name = "test-service"
        mock_csr.sans_dns = {"api.example.com", "web.example.com"}
        mock_csr.sans_ip = {"10.0.0.1"}

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify no regeneration occurred (sync should not be called)
        self.handler.interface.sync.assert_not_called()

    def test_validate_and_regenerate_dns_sans_mismatch(self) -> None:
        """Test validation when DNS SANs mismatch - triggers regeneration."""
        # Mock expected certificate request
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = ["api.example.com", "new.example.com"]
        mock_expected_request.sans_ip = ["10.0.0.1"]

        # Mock CSR with different DNS SANs
        mock_csr = MagicMock()
        mock_csr.common_name = "test-service"
        mock_csr.sans_dns = {"api.example.com", "old.example.com"}
        mock_csr.sans_ip = {"10.0.0.1"}

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify certificates were regenerated
        self.assertEqual(
            self.handler.interface.certificate_requests,
            [mock_expected_request],
        )
        self.handler.interface.sync.assert_called_once()

    def test_validate_and_regenerate_ip_sans_mismatch(self) -> None:
        """Test validation when IP SANs mismatch - triggers regeneration."""
        # Mock expected certificate request
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = ["api.example.com"]
        mock_expected_request.sans_ip = ["10.0.0.1", "10.0.0.2"]

        # Mock CSR with different IP SANs
        mock_csr = MagicMock()
        mock_csr.common_name = "test-service"
        mock_csr.sans_dns = {"api.example.com"}
        mock_csr.sans_ip = {"10.0.0.1"}

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify certificates were regenerated
        self.assertEqual(
            self.handler.interface.certificate_requests,
            [mock_expected_request],
        )
        self.handler.interface.sync.assert_called_once()

    def test_validate_and_regenerate_missing_common_name(self) -> None:
        """Test validation when expected common name not in relation CSRs."""
        # Mock expected certificate request
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "new-service"
        mock_expected_request.sans_dns = ["api.example.com"]
        mock_expected_request.sans_ip = ["10.0.0.1"]

        # Mock CSR with different common name
        mock_csr = MagicMock()
        mock_csr.common_name = "old-service"
        mock_csr.sans_dns = {"api.example.com"}
        mock_csr.sans_ip = {"10.0.0.1"}

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify certificates were regenerated
        self.assertEqual(
            self.handler.interface.certificate_requests,
            [mock_expected_request],
        )
        self.handler.interface.sync.assert_called_once()

    def test_validate_and_regenerate_empty_sans(self) -> None:
        """Test validation with empty SANs on both sides."""
        # Mock expected certificate request with empty SANs
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = []
        mock_expected_request.sans_ip = []

        # Mock CSR with empty SANs
        mock_csr = MagicMock()
        mock_csr.common_name = "test-service"
        mock_csr.sans_dns = set()
        mock_csr.sans_ip = set()

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify no regeneration occurred (empty SANs match)
        self.handler.interface.sync.assert_not_called()

    def test_validate_and_regenerate_none_sans(self) -> None:
        """Test validation with None SANs in expected requests."""
        # Mock expected certificate request with None SANs
        mock_expected_request = MagicMock()
        mock_expected_request.common_name = "test-service"
        mock_expected_request.sans_dns = None
        mock_expected_request.sans_ip = None

        # Mock CSR with empty SANs
        mock_csr = MagicMock()
        mock_csr.common_name = "test-service"
        mock_csr.sans_dns = set()
        mock_csr.sans_ip = set()

        mock_requirer_cert = MagicMock()
        mock_requirer_cert.certificate_signing_request = mock_csr

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request]
        )

        # Verify no regeneration occurred (None converts to empty set)
        self.handler.interface.sync.assert_not_called()

    def test_validate_and_regenerate_multiple_certificates(self) -> None:
        """Test validation with multiple certificate requests."""
        # Mock expected certificate requests
        mock_expected_request1 = MagicMock()
        mock_expected_request1.common_name = "service1"
        mock_expected_request1.sans_dns = ["api1.example.com"]
        mock_expected_request1.sans_ip = ["10.0.0.1"]

        mock_expected_request2 = MagicMock()
        mock_expected_request2.common_name = "service2"
        mock_expected_request2.sans_dns = ["api2.example.com"]
        mock_expected_request2.sans_ip = ["10.0.0.2"]

        # Mock CSRs - first matches, second doesn't
        mock_csr1 = MagicMock()
        mock_csr1.common_name = "service1"
        mock_csr1.sans_dns = {"api1.example.com"}
        mock_csr1.sans_ip = {"10.0.0.1"}

        mock_csr2 = MagicMock()
        mock_csr2.common_name = "service2"
        mock_csr2.sans_dns = {"api2.example.com"}
        mock_csr2.sans_ip = {"10.0.0.99"}  # Mismatch

        mock_requirer_cert1 = MagicMock()
        mock_requirer_cert1.certificate_signing_request = mock_csr1

        mock_requirer_cert2 = MagicMock()
        mock_requirer_cert2.certificate_signing_request = mock_csr2

        self.handler.interface.get_csrs_from_requirer_relation_data.return_value = [
            mock_requirer_cert1,
            mock_requirer_cert2,
        ]

        # Call the validation function
        self.handler.validate_and_regenerate_certificates_if_needed(
            [mock_expected_request1, mock_expected_request2]
        )

        # Verify certificates were regenerated due to mismatch in second cert
        self.assertEqual(
            self.handler.interface.certificate_requests,
            [mock_expected_request1, mock_expected_request2],
        )
        self.handler.interface.sync.assert_called_once()

    def test_validate_and_regenerate_uses_default_requests(self) -> None:
        """Test that default_certificate_requests is used when expected_cert_requests is None."""
        mock_default_request = MagicMock()
        mock_default_request.common_name = "default-service"
        mock_default_request.sans_dns = ["default.example.com"]
        mock_default_request.sans_ip = ["10.0.0.1"]

        with patch.object(
            self.handler,
            "default_certificate_requests",
            return_value=[mock_default_request],
        ):
            # Mock empty CSRs from relation
            self.handler.interface.get_csrs_from_requirer_relation_data.return_value = (
                []
            )

            # Call with None (should use default)
            self.handler.validate_and_regenerate_certificates_if_needed(None)

            # Verify default_certificate_requests was called
            self.handler.default_certificate_requests.assert_called_once()

            # Verify certificates were regenerated with default requests
            self.assertEqual(
                self.handler.interface.certificate_requests,
                [mock_default_request],
            )
            self.handler.interface.sync.assert_called_once()


if __name__ == "__main__":
    import unittest

    unittest.main()
