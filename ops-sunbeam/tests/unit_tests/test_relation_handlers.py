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

"""Test relation handlers."""

import json
from unittest.mock import (
    MagicMock,
    patch,
)

import ops
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.test_utils as test_utils
from ops import (
    ModelError,
    SecretNotFoundError,
)


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


class TestUserIdentityResourceRequiresHandler(test_utils.CharmTestCase):
    """Tests for UserIdentityResourceRequiresHandler."""

    PATCHES = []

    def setUp(self) -> None:
        """Set up the test environment."""
        super().setUp(test_utils, self.PATCHES)
        self.mock_charm = MagicMock()
        self.mock_callback = MagicMock()
        self._leader_data: dict[str, str] = {}

        # leader_get / leader_set backed by a real dict
        self.mock_charm.leader_get.side_effect = self._leader_data.get
        self.mock_charm.leader_set.side_effect = self._leader_data.update
        self.mock_charm.model.unit.is_leader.return_value = True
        # handler.model (from ops.framework.Object) resolves through
        # charm.framework.model — link it to charm.model so mocks work.
        self.mock_charm.framework.model = self.mock_charm.model

        self._create_handler()

    # ------------------------------------------------------------------
    # Helper factories
    # ------------------------------------------------------------------

    def _create_handler(
        self,
        name: str = "test-user",
        domain: str = "test-domain",
        role: str | None = None,
        add_suffix: bool = False,
        rotate: ops.SecretRotate = ops.SecretRotate.NEVER,
        extra_ops: list | None = None,
        extra_ops_process=None,
        project: str | None = None,
        project_domain: str | None = None,
    ):
        """Build a handler with mocked lifecycle."""
        with patch.object(
            sunbeam_rhandlers.UserIdentityResourceRequiresHandler,
            "setup_event_handler",
            return_value=MagicMock(),
        ), patch.object(
            sunbeam_rhandlers.UserIdentityResourceRequiresHandler,
            "__post_init__",
            return_value=None,
        ):
            self.handler = (
                sunbeam_rhandlers.UserIdentityResourceRequiresHandler(
                    charm=self.mock_charm,
                    relation_name="identity-ops",
                    callback_f=self.mock_callback,
                    mandatory=True,
                    name=name,
                    domain=domain,
                    role=role,
                    add_suffix=add_suffix,
                    rotate=rotate,
                    extra_ops=extra_ops,
                    extra_ops_process=extra_ops_process,
                    project=project,
                    project_domain=project_domain,
                )
            )
        self.handler.interface = MagicMock()

    def _make_create_user_response(
        self,
        username: str = "test-user",
        secret_id: str = "secret:abc123",
        return_code: int = 0,
        extra_ops_responses: list[dict] | None = None,
    ) -> dict:
        """Build a successful create_user response payload."""
        ops_list = [
            {
                "name": "create_domain",
                "return-code": return_code,
                "value": {"name": "test-domain"},
            },
            {
                "name": "create_user",
                "return-code": return_code,
                "value": {"name": username},
                "secret-id": secret_id,
            },
        ]
        if extra_ops_responses:
            ops_list.extend(extra_ops_responses)
        return {
            "id": "hash123",
            "tag": self.handler._create_user_tag,
            "ops": ops_list,
        }

    def _make_delete_user_response(
        self,
        users: list[str],
        return_codes: list[int] | None = None,
    ) -> dict:
        """Build a delete_user response payload."""
        if return_codes is None:
            return_codes = [0] * len(users)
        ops_list = [
            {
                "name": "delete_user",
                "return-code": rc,
                "value": {"name": u},
            }
            for u, rc in zip(users, return_codes)
        ]
        return {
            "id": "hash-del",
            "tag": self.handler._delete_user_tag,
            "ops": ops_list,
        }

    def _store_credentials(
        self,
        username: str = "test-user",
        password_secret: str = "secret:abc123",
    ):
        """Persist credentials in leader data (simulates a previous response)."""
        self._leader_data[self.handler.credentials_secret_label] = json.dumps(
            {"username": username, "password": password_secret}
        )

    def _store_config_secret(self, secret_id: str = "secret:cfg1"):
        """Persist the config secret id in leader data."""
        self._leader_data[self.handler.config_label] = secret_id

    # ------------------------------------------------------------------
    # Property / label tests
    # ------------------------------------------------------------------

    def test_labels(self) -> None:
        """Verify label properties use the correct username."""
        self.assertEqual(
            self.handler.credentials_secret_label,
            "user-identity-resource-test-user",
        )
        self.assertEqual(
            self.handler.config_label,
            "configure-credential-test-user",
        )

    def test_tags(self) -> None:
        """Verify tag properties."""
        self.assertEqual(
            self.handler._create_user_tag, "create_user_test-user"
        )
        self.assertEqual(
            self.handler._delete_user_tag, "delete_user_test-user"
        )

    # ------------------------------------------------------------------
    # ensure_username
    # ------------------------------------------------------------------

    def test_ensure_username_first_call(self) -> None:
        """First call stores the username and returns it."""
        name = self.handler.ensure_username("myuser")
        self.assertEqual(name, "myuser")
        stored = json.loads(
            self._leader_data[self.handler.credentials_secret_label]
        )
        self.assertEqual(stored, {"username": "myuser", "password": ""})

    def test_ensure_username_with_suffix(self) -> None:
        """With add_suffix, a random suffix is appended."""
        with patch(
            "ops_sunbeam.relation_handlers.random_string", return_value="abcde"
        ):
            name = self.handler.ensure_username("myuser", add_suffix=True)
        self.assertEqual(name, "myuser-abcde")

    def test_ensure_username_idempotent(self) -> None:
        """Subsequent calls return the stored username, ignoring add_suffix."""
        self._store_credentials("stored-user", "")
        name = self.handler.ensure_username("ignored", add_suffix=True)
        self.assertEqual(name, "stored-user")

    # ------------------------------------------------------------------
    # _get_credentials
    # ------------------------------------------------------------------

    def test_get_credentials_none_when_empty(self) -> None:
        """Returns None when nothing stored."""
        self.assertIsNone(self.handler._get_credentials())

    def test_get_credentials_none_when_password_empty(self) -> None:
        """Returns None when password is empty string (pre-response state)."""
        self._store_credentials("test-user", "")
        self.assertIsNone(self.handler._get_credentials())

    def test_get_credentials_returns_pair(self) -> None:
        """Returns (username, password) when fully populated."""
        self._store_credentials("test-user", "secret:abc")
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {"password": "hunter2"}
        self.mock_charm.model.get_secret.return_value = mock_secret

        result = self.handler._get_credentials()
        self.assertEqual(result, ("test-user", "hunter2"))
        self.mock_charm.model.get_secret.assert_called_once_with(
            id="secret:abc"
        )

    # ------------------------------------------------------------------
    # get_config_credentials
    # ------------------------------------------------------------------

    def test_get_config_credentials_none(self) -> None:
        """Returns None when no config secret stored."""
        self.assertIsNone(self.handler.get_config_credentials())

    def test_get_config_credentials(self) -> None:
        """Returns (username, password) from config secret."""
        self._store_config_secret("secret:cfg1")
        mock_secret = MagicMock()
        mock_secret.get_content.return_value = {
            "username": "u",
            "password": "p",
        }
        self.mock_charm.model.get_secret.return_value = mock_secret

        self.assertEqual(self.handler.get_config_credentials(), ("u", "p"))

    # ------------------------------------------------------------------
    # ready property
    # ------------------------------------------------------------------

    def test_ready_false_when_no_config(self) -> None:
        """Not ready when there are no config credentials."""
        self.assertFalse(self.handler.ready)

    def test_ready_true_when_config_present(self) -> None:
        """Ready when get_config_credentials returns a tuple."""
        with patch.object(
            self.handler,
            "get_config_credentials",
            return_value=("u", "p"),
        ):
            self.assertTrue(self.handler.ready)

    # ------------------------------------------------------------------
    # _create_user_request
    # ------------------------------------------------------------------

    def test_create_user_request_minimal(self) -> None:
        """Minimal request has create_domain and create_user."""
        req = self.handler._create_user_request()

        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("create_domain", op_names)
        self.assertIn("create_user", op_names)
        self.assertEqual(req["tag"], self.handler._create_user_tag)

        create_user_op = next(
            op for op in req["ops"] if op["name"] == "create_user"
        )
        self.assertIn("secret-request", create_user_op)
        self.assertEqual(
            create_user_op["secret-request"]["secret-params"], ["password"]
        )
        self.assertEqual(
            create_user_op["secret-request"]["secret-label"],
            self.handler.credentials_secret_label,
        )

    def test_create_user_request_with_role(self) -> None:
        """Request includes create_role and grant_role when role is set."""
        self._create_handler(name="admin", domain="default", role="admin")
        req = self.handler._create_user_request()

        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("create_role", op_names)
        self.assertIn("grant_role", op_names)

    def test_create_user_request_with_project_grant(self) -> None:
        """Request includes show_project and two grant_role ops for project."""
        self._create_handler(
            name="svc",
            domain="svc-domain",
            role="member",
            project="services",
            project_domain="svc-domain",
        )
        req = self.handler._create_user_request()

        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("show_project", op_names)
        # Two grant_role ops: domain + project
        self.assertEqual(op_names.count("grant_role"), 2)

    def test_create_user_request_with_extra_ops_dict(self) -> None:
        """Extra ops as dicts are appended to the request."""
        extra = [{"name": "custom_op", "params": {"key": "val"}}]
        self._create_handler(
            name="test-user", domain="test-domain", extra_ops=extra
        )
        req = self.handler._create_user_request()

        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("custom_op", op_names)

    def test_create_user_request_with_extra_ops_callable(self) -> None:
        """Extra ops as callables are evaluated and appended."""
        extra = [lambda: {"name": "dynamic_op", "params": {}}]
        self._create_handler(
            name="test-user", domain="test-domain", extra_ops=extra
        )
        req = self.handler._create_user_request()

        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("dynamic_op", op_names)

    def test_create_user_request_invalid_extra_op_skipped(self) -> None:
        """Invalid extra op types are silently skipped."""
        extra = [42]  # not dict, not callable
        self._create_handler(
            name="test-user", domain="test-domain", extra_ops=extra
        )
        req = self.handler._create_user_request()
        # Should not crash, and 42 should not appear
        op_names = [op["name"] for op in req["ops"]]
        self.assertIn("create_user", op_names)

    def test_create_user_request_idempotent_username(self) -> None:
        """Subsequent calls reuse the stored username."""
        self._store_credentials("already-stored", "")
        req = self.handler._create_user_request()
        create_user_op = next(
            op for op in req["ops"] if op["name"] == "create_user"
        )
        self.assertEqual(create_user_op["params"]["name"], "already-stored")

    # ------------------------------------------------------------------
    # _delete_user_request
    # ------------------------------------------------------------------

    def test_delete_user_request(self) -> None:
        """Delete request contains one op per user with domain."""
        req = self.handler._delete_user_request(["alice", "bob"])

        self.assertEqual(req["tag"], self.handler._delete_user_tag)
        self.assertEqual(len(req["ops"]), 2)
        names = [op["params"]["name"] for op in req["ops"]]
        self.assertEqual(names, ["alice", "bob"])
        # domain propagated
        self.assertEqual(req["ops"][0]["params"]["domain"], "test-domain")

    # ------------------------------------------------------------------
    # Normal workflow: provider_ready → response_available
    # ------------------------------------------------------------------

    def test_on_provider_ready_sends_request(self) -> None:
        """provider_ready sends create_user request and calls callback."""
        event = MagicMock()
        self.handler._on_provider_ready(event)

        self.handler.interface.request_ops.assert_called_once()
        self.mock_callback.assert_called_once_with(event)

    def test_on_provider_ready_non_leader_noop(self) -> None:
        """Non-leader units do nothing on provider_ready."""
        self.mock_charm.model.unit.is_leader.return_value = False
        event = MagicMock()
        self.handler._on_provider_ready(event)

        self.handler.interface.request_ops.assert_not_called()

    def test_process_create_user_response_first_time(self) -> None:
        """First successful response stores credentials and creates config secret."""
        response = self._make_create_user_response(
            username="test-user", secret_id="secret:pw1"
        )
        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "actual-pass"}
        self.mock_charm.model.get_secret.return_value = mock_pw_secret

        mock_app_secret = MagicMock()
        mock_app_secret.id = "secret:cfg-new"
        self.mock_charm.model.app.add_secret.return_value = mock_app_secret

        self.handler._process_create_user_response(response)

        # credentials stored
        stored = json.loads(
            self._leader_data[self.handler.credentials_secret_label]
        )
        self.assertEqual(stored["username"], "test-user")
        self.assertEqual(stored["password"], "secret:pw1")

        # config secret created with resolved password
        self.mock_charm.model.app.add_secret.assert_called_once()
        call_args = self.mock_charm.model.app.add_secret.call_args
        self.assertEqual(
            call_args[0][0],
            {"username": "test-user", "password": "actual-pass"},
        )

    def test_process_create_user_response_updates_existing_config(
        self,
    ) -> None:
        """When config secret already exists, set_content is called."""
        self._store_credentials("test-user", "secret:old")
        self._store_config_secret("secret:cfg-existing")

        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "newpass"}

        mock_cfg_secret = MagicMock()
        mock_cfg_secret.get_content.return_value = {
            "username": "test-user",
            "password": "oldpass",
        }

        def get_secret_side_effect(id):
            if id == "secret:pw-new":
                return mock_pw_secret
            if id == "secret:cfg-existing":
                return mock_cfg_secret
            raise SecretNotFoundError("not found")

        self.mock_charm.model.get_secret.side_effect = get_secret_side_effect

        response = self._make_create_user_response(
            username="test-user", secret_id="secret:pw-new"
        )
        self.handler._process_create_user_response(response)

        mock_cfg_secret.set_content.assert_called_once_with(
            {"username": "test-user", "password": "newpass"}
        )

    def test_process_create_user_response_detects_old_creds(self) -> None:
        """When credentials change, old username is added to delete list."""
        # Pre-existing config credentials
        self._store_config_secret("secret:cfg1")

        mock_cfg_secret = MagicMock()
        mock_cfg_secret.get_content.return_value = {
            "username": "old-user",
            "password": "old-pass",
        }

        mock_new_pw_secret = MagicMock()
        mock_new_pw_secret.get_content.return_value = {"password": "new-pass"}

        def get_secret_side_effect(id):
            if id == "secret:cfg1":
                return mock_cfg_secret
            if id == "secret:pw-new":
                return mock_new_pw_secret
            raise SecretNotFoundError(id)

        self.mock_charm.model.get_secret.side_effect = get_secret_side_effect

        response = self._make_create_user_response(
            username="new-user", secret_id="secret:pw-new"
        )
        self.handler._process_create_user_response(response)

        # old-user should be in the delete list
        old_users = json.loads(self._leader_data.get("old_users", "[]"))
        self.assertIn("old-user", old_users)

    def test_on_response_available_create_tag(self) -> None:
        """response_available dispatches to create handler on create tag."""
        response = self._make_create_user_response()
        self.handler.interface.response = response

        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "pass1"}
        self.mock_charm.model.get_secret.return_value = mock_pw_secret

        mock_app_secret = MagicMock()
        mock_app_secret.id = "secret:cfg-new"
        self.mock_charm.model.app.add_secret.return_value = mock_app_secret

        event = MagicMock()
        self.handler._on_response_available(event)
        self.mock_callback.assert_called_once_with(event)

    def test_on_response_available_delete_tag(self) -> None:
        """response_available dispatches to delete handler on delete tag."""
        self._leader_data["old_users"] = json.dumps(["alice"])
        response = self._make_delete_user_response(["alice"])
        self.handler.interface.response = response

        event = MagicMock()
        self.handler._on_response_available(event)

        old_users = json.loads(self._leader_data.get("old_users", "[]"))
        self.assertNotIn("alice", old_users)
        self.mock_callback.assert_called_once_with(event)

    def test_on_response_available_extra_ops_process(self) -> None:
        """extra_ops_process callback is invoked on create response."""
        mock_extra_process = MagicMock()
        self._create_handler(
            name="test-user",
            domain="test-domain",
            extra_ops_process=mock_extra_process,
        )
        response = self._make_create_user_response()
        self.handler.interface.response = response

        mock_app_secret = MagicMock()
        mock_app_secret.id = "secret:cfg-new"
        self.mock_charm.model.app.add_secret.return_value = mock_app_secret

        event = MagicMock()
        self.handler._on_response_available(event)
        mock_extra_process.assert_called_once_with(event, response)

    def test_on_response_available_non_leader_noop(self) -> None:
        """Non-leader ignores response_available."""
        self.mock_charm.model.unit.is_leader.return_value = False
        self.handler.interface.response = self._make_create_user_response()
        event = MagicMock()
        self.handler._on_response_available(event)
        # callback should not be called
        self.mock_callback.assert_not_called()

    # ------------------------------------------------------------------
    # Error in response
    # ------------------------------------------------------------------

    def test_process_create_user_response_error_return_code(self) -> None:
        """Response with non-zero return-code is ignored."""
        response = self._make_create_user_response(return_code=1)
        self.handler._process_create_user_response(response)

        # No credentials should be stored
        self.assertNotIn(
            self.handler.credentials_secret_label, self._leader_data
        )

    def test_process_create_user_response_mixed_return_codes(self) -> None:
        """If any op has non-zero return-code, whole response is skipped."""
        response = self._make_create_user_response()
        # Inject one failing op
        response["ops"].append(
            {"name": "grant_role", "return-code": 1, "value": {}}
        )
        self.handler._process_create_user_response(response)
        self.assertNotIn(
            self.handler.credentials_secret_label, self._leader_data
        )

    def test_process_create_user_response_no_secret_id(self) -> None:
        """Response missing secret-id in create_user op is ignored."""
        response = self._make_create_user_response()
        create_op = next(
            op for op in response["ops"] if op["name"] == "create_user"
        )
        del create_op["secret-id"]
        self.handler._process_create_user_response(response)
        # Credentials should not be updated (password stays empty)
        self.assertNotIn(
            self.handler.credentials_secret_label, self._leader_data
        )

    def test_process_create_user_response_no_username_in_value(self) -> None:
        """Response missing name in create_user value is ignored."""
        response = self._make_create_user_response()
        create_op = next(
            op for op in response["ops"] if op["name"] == "create_user"
        )
        create_op["value"] = {}
        self.handler._process_create_user_response(response)
        self.assertNotIn(
            self.handler.credentials_secret_label, self._leader_data
        )

    def test_process_create_user_response_no_create_user_op(self) -> None:
        """Response without create_user op is ignored."""
        response = {
            "id": "hash",
            "tag": self.handler._create_user_tag,
            "ops": [
                {
                    "name": "create_domain",
                    "return-code": 0,
                    "value": {"name": "d"},
                }
            ],
        }
        self.handler._process_create_user_response(response)
        self.assertNotIn(
            self.handler.credentials_secret_label, self._leader_data
        )

    def test_process_delete_user_response_partial_failure(self) -> None:
        """Only successfully deleted users are removed from list."""
        self._leader_data["old_users"] = json.dumps(["alice", "bob"])
        response = self._make_delete_user_response(
            ["alice", "bob"], return_codes=[0, 1]
        )

        self.handler._process_delete_user_response(response)

        remaining = json.loads(self._leader_data["old_users"])
        self.assertNotIn("alice", remaining)
        self.assertIn("bob", remaining)

    def test_process_delete_user_response_all_fail(self) -> None:
        """When all deletes fail, the list stays unchanged."""
        self._leader_data["old_users"] = json.dumps(["alice", "bob"])
        response = self._make_delete_user_response(
            ["alice", "bob"], return_codes=[1, 1]
        )

        self.handler._process_delete_user_response(response)

        remaining = json.loads(self._leader_data["old_users"])
        self.assertEqual(sorted(remaining), ["alice", "bob"])

    def test_process_delete_user_response_empty_list(self) -> None:
        """Handles empty old_users list without error."""
        self._leader_data["old_users"] = json.dumps([])
        response = self._make_delete_user_response(["gone"])

        self.handler._process_delete_user_response(response)
        remaining = json.loads(self._leader_data["old_users"])
        self.assertEqual(remaining, [])

    # ------------------------------------------------------------------
    # Rotate workflow
    # ------------------------------------------------------------------

    def test_on_secret_rotate_triggers_new_request(self) -> None:
        """Rotation clears credentials and sends a new create_user request."""
        self._store_credentials("old-user", "secret:old")
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_rotate(event)

        # new request sent (ensure_username re-populates credentials
        # with new username + empty password)
        self.handler.interface.request_ops.assert_called_once()
        sent_request = self.handler.interface.request_ops.call_args[0][0]
        self.assertEqual(sent_request["tag"], self.handler._create_user_tag)

    def test_on_secret_rotate_non_leader_noop(self) -> None:
        """Non-leader ignores rotate events."""
        self.mock_charm.model.unit.is_leader.return_value = False
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_rotate(event)
        self.handler.interface.request_ops.assert_not_called()

    def test_on_secret_rotate_wrong_label_ignored(self) -> None:
        """Rotate events for non-config secrets are ignored."""
        event = MagicMock()
        event.secret.label = "some-other-secret"

        self.handler._on_secret_rotate(event)
        self.handler.interface.request_ops.assert_not_called()

    def test_rotate_generates_new_username_with_suffix(self) -> None:
        """After rotation clears credentials, a new suffixed username is generated."""
        self._create_handler(
            name="svc-user", domain="test-domain", add_suffix=True
        )
        self._store_credentials("svc-user-xxxxx", "secret:old")

        event = MagicMock()
        event.secret.label = self.handler.config_label

        with patch(
            "ops_sunbeam.relation_handlers.random_string", return_value="zzzzz"
        ):
            self.handler._on_secret_rotate(event)

        sent = self.handler.interface.request_ops.call_args[0][0]
        create_user_op = next(
            op for op in sent["ops"] if op["name"] == "create_user"
        )
        self.assertEqual(create_user_op["params"]["name"], "svc-user-zzzzz")

    def test_full_rotation_cycle_old_user_in_delete_list(self) -> None:
        """Full cycle: rotate → response → old user queued for deletion."""
        # 1. Pre-existing config credentials
        self._store_credentials("old-user", "secret:old-pw")
        self._store_config_secret("secret:cfg1")

        mock_cfg_secret = MagicMock()
        mock_cfg_secret.get_content.return_value = {
            "username": "old-user",
            "password": "old-pass",
        }

        # 2. Rotate event
        rotate_event = MagicMock()
        rotate_event.secret.label = self.handler.config_label
        self.handler._on_secret_rotate(rotate_event)

        # 3. Simulate response from keystone
        mock_new_pw = MagicMock()
        mock_new_pw.get_content.return_value = {"password": "new-pass"}

        def get_secret_side_effect(id):
            if id == "secret:cfg1":
                return mock_cfg_secret
            if id == "secret:new-pw":
                return mock_new_pw
            raise SecretNotFoundError(id)

        self.mock_charm.model.get_secret.side_effect = get_secret_side_effect

        response = self._make_create_user_response(
            username="new-user", secret_id="secret:new-pw"
        )
        self.handler._process_create_user_response(response)

        # old-user should now be in the delete list
        old_users = json.loads(self._leader_data.get("old_users", "[]"))
        self.assertIn("old-user", old_users)

    # ------------------------------------------------------------------
    # secret-changed
    # ------------------------------------------------------------------

    def test_on_secret_changed_config_label_calls_callback(self) -> None:
        """secret-changed on config label invokes the charm callback."""
        event = MagicMock()
        event.secret.label = self.handler.config_label
        self.handler._on_secret_changed(event)
        self.mock_callback.assert_called_once_with(event)

    def test_on_secret_changed_other_label_ignored(self) -> None:
        """secret-changed on unrelated label is ignored."""
        event = MagicMock()
        event.secret.label = "unrelated-secret"
        self.handler._on_secret_changed(event)
        self.mock_callback.assert_not_called()

    # ------------------------------------------------------------------
    # secret-remove
    # ------------------------------------------------------------------

    def test_on_secret_remove_sends_delete_request(self) -> None:
        """secret-remove sends delete request for queued users."""
        self._leader_data["old_users"] = json.dumps(["alice", "bob"])
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_remove(event)

        self.handler.interface.request_ops.assert_called_once()
        sent = self.handler.interface.request_ops.call_args[0][0]
        self.assertEqual(sent["tag"], self.handler._delete_user_tag)
        names = [op["params"]["name"] for op in sent["ops"]]
        self.assertEqual(sorted(names), ["alice", "bob"])

    def test_on_secret_remove_empty_list_noop(self) -> None:
        """secret-remove with no queued users sends nothing."""
        self._leader_data["old_users"] = json.dumps([])
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_remove(event)
        self.handler.interface.request_ops.assert_not_called()

    def test_on_secret_remove_no_old_users_key_noop(self) -> None:
        """secret-remove with no old_users key at all sends nothing."""
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_remove(event)
        self.handler.interface.request_ops.assert_not_called()

    def test_on_secret_remove_wrong_label_ignored(self) -> None:
        """secret-remove for non-config label is ignored."""
        self._leader_data["old_users"] = json.dumps(["alice"])
        event = MagicMock()
        event.secret.label = "unrelated"

        self.handler._on_secret_remove(event)
        self.handler.interface.request_ops.assert_not_called()

    def test_on_secret_remove_non_leader_noop(self) -> None:
        """Non-leader ignores secret-remove."""
        self.mock_charm.model.unit.is_leader.return_value = False
        self._leader_data["old_users"] = json.dumps(["alice"])
        event = MagicMock()
        event.secret.label = self.handler.config_label

        self.handler._on_secret_remove(event)
        self.handler.interface.request_ops.assert_not_called()

    # ------------------------------------------------------------------
    # _clean_old_credentials
    # ------------------------------------------------------------------

    def test_clean_old_credentials_removes_secret(self) -> None:
        """Cleaning old credentials removes the old-style secret and requests new user."""
        self._leader_data[self.handler.credentials_secret_label] = (
            "secret:old123"
        )
        mock_secret = MagicMock()
        self.mock_charm.model.get_secret.return_value = mock_secret

        self.handler._clean_old_credentials()

        self.mock_charm.model.get_secret.assert_called_once_with(
            id="secret:old123"
        )
        mock_secret.remove_all_revisions.assert_called_once()
        # After removing old secret, a new user request is sent
        self.handler.interface.request_ops.assert_called_once()
        # The credentials_secret_label now contains new credentials JSON
        credentials = json.loads(
            self._leader_data[self.handler.credentials_secret_label]
        )
        self.assertEqual(credentials["username"], "test-user")
        self.assertEqual(credentials["password"], "")

    def test_clean_old_credentials_already_gone(self) -> None:
        """Handles SecretNotFoundError gracefully."""
        self._leader_data[self.handler.credentials_secret_label] = (
            "secret:gone"
        )
        self.mock_charm.model.get_secret.side_effect = SecretNotFoundError(
            "gone"
        )

        self.handler._clean_old_credentials()
        # Should not raise; label is NOT cleared on error
        self.assertEqual(
            self._leader_data[self.handler.credentials_secret_label],
            "secret:gone",
        )

    def test_clean_old_credentials_model_error(self) -> None:
        """Handles ModelError gracefully."""
        self._leader_data[self.handler.credentials_secret_label] = (
            "secret:broken"
        )
        self.mock_charm.model.get_secret.side_effect = ModelError("boom")

        self.handler._clean_old_credentials()
        # Label is NOT cleared on error
        self.assertEqual(
            self._leader_data[self.handler.credentials_secret_label],
            "secret:broken",
        )

    def test_clean_old_credentials_noop_when_empty(self) -> None:
        """No-op when there is no old secret id stored."""
        self.handler._clean_old_credentials()
        self.mock_charm.model.get_secret.assert_not_called()

    def test_clean_old_credentials_non_leader_noop(self) -> None:
        """Non-leader does not attempt cleanup."""
        self.mock_charm.model.unit.is_leader.return_value = False
        self._leader_data[self.handler.credentials_secret_label] = (
            "secret:old123"
        )

        self.handler._clean_old_credentials()
        self.mock_charm.model.get_secret.assert_not_called()

    # ------------------------------------------------------------------
    # update_relation_data
    # ------------------------------------------------------------------

    def test_update_relation_data_calls_clean(self) -> None:
        """update_relation_data delegates to _clean_old_credentials."""
        with patch.object(
            self.handler, "_clean_old_credentials"
        ) as mock_clean:
            self.handler.update_relation_data()
            mock_clean.assert_called_once()

    # ------------------------------------------------------------------
    # _update_config_credentials
    # ------------------------------------------------------------------

    def test_update_config_credentials_creates_new(self) -> None:
        """Creates config secret when none exists."""
        self._store_credentials("test-user", "secret:pw1")
        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "mypass"}
        self.mock_charm.model.get_secret.return_value = mock_pw_secret

        mock_app_secret = MagicMock()
        mock_app_secret.id = "secret:cfg-new"
        self.mock_charm.model.app.add_secret.return_value = mock_app_secret

        result = self.handler._update_config_credentials()

        self.assertTrue(result)
        self.mock_charm.model.app.add_secret.assert_called_once()
        call_args = self.mock_charm.model.app.add_secret.call_args
        self.assertEqual(
            call_args[0][0],
            {"username": "test-user", "password": "mypass"},
        )
        self.assertEqual(call_args[1]["label"], self.handler.config_label)
        self.assertEqual(
            self._leader_data[self.handler.config_label], "secret:cfg-new"
        )

    def test_update_config_credentials_respects_rotate(self) -> None:
        """Config secret is created with the configured rotate policy."""
        self._create_handler(
            name="test-user",
            domain="test-domain",
            rotate=ops.SecretRotate.DAILY,
        )
        self._store_credentials("test-user", "secret:pw1")
        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "mypass"}
        self.mock_charm.model.get_secret.return_value = mock_pw_secret

        mock_app_secret = MagicMock()
        mock_app_secret.id = "secret:cfg-new"
        self.mock_charm.model.app.add_secret.return_value = mock_app_secret

        self.handler._update_config_credentials()

        call_kwargs = self.mock_charm.model.app.add_secret.call_args[1]
        self.assertEqual(call_kwargs["rotate"], ops.SecretRotate.DAILY)

    def test_update_config_credentials_no_change(self) -> None:
        """Returns False when content is unchanged."""
        self._store_credentials("test-user", "secret:pw1")
        self._store_config_secret("secret:cfg1")

        mock_pw_secret = MagicMock()
        mock_pw_secret.get_content.return_value = {"password": "same"}

        mock_cfg_secret = MagicMock()
        mock_cfg_secret.get_content.return_value = {
            "username": "test-user",
            "password": "same",
        }

        def get_secret_side_effect(id):
            if id == "secret:pw1":
                return mock_pw_secret
            if id == "secret:cfg1":
                return mock_cfg_secret
            raise SecretNotFoundError(id)

        self.mock_charm.model.get_secret.side_effect = get_secret_side_effect

        result = self.handler._update_config_credentials()
        self.assertFalse(result)
        mock_cfg_secret.set_content.assert_not_called()

    def test_update_config_credentials_no_creds_returns_false(self) -> None:
        """Returns False when _get_credentials returns None."""
        result = self.handler._update_config_credentials()
        self.assertFalse(result)

    # ------------------------------------------------------------------
    # add_user_to_delete_user_list
    # ------------------------------------------------------------------

    def test_add_user_to_delete_list_new(self) -> None:
        """Adds user to empty delete list."""
        self.handler.add_user_to_delete_user_list("alice")
        users = json.loads(self._leader_data["old_users"])
        self.assertEqual(users, ["alice"])

    def test_add_user_to_delete_list_duplicate(self) -> None:
        """Does not duplicate an existing entry."""
        self._leader_data["old_users"] = json.dumps(["alice"])
        self.handler.add_user_to_delete_user_list("alice")
        users = json.loads(self._leader_data["old_users"])
        self.assertEqual(users, ["alice"])

    def test_add_user_to_delete_list_appends(self) -> None:
        """Appends to existing list."""
        self._leader_data["old_users"] = json.dumps(["alice"])
        self.handler.add_user_to_delete_user_list("bob")
        users = json.loads(self._leader_data["old_users"])
        self.assertEqual(sorted(users), ["alice", "bob"])

    # ------------------------------------------------------------------
    # _find_op helper
    # ------------------------------------------------------------------

    def test_find_op_found(self) -> None:
        """Returns the matching op dict."""
        resp = {"ops": [{"name": "a"}, {"name": "b"}]}
        self.assertEqual(self.handler._find_op(resp, "b"), {"name": "b"})

    def test_find_op_not_found(self) -> None:
        """Returns None when op is not present."""
        resp = {"ops": [{"name": "a"}]}
        self.assertIsNone(self.handler._find_op(resp, "z"))

    def test_find_op_empty_ops(self) -> None:
        """Returns None on empty ops list."""
        self.assertIsNone(self.handler._find_op({"ops": []}, "a"))

    def test_find_op_no_ops_key(self) -> None:
        """Returns None when response has no ops key."""
        self.assertIsNone(self.handler._find_op({}, "a"))

    # ------------------------------------------------------------------
    # _hash_ops
    # ------------------------------------------------------------------

    def test_hash_ops_deterministic(self) -> None:
        """Same input produces same hash."""
        ops_list = [{"name": "create_user", "params": {"name": "u"}}]
        h1 = self.handler._hash_ops(ops_list)
        h2 = self.handler._hash_ops(ops_list)
        self.assertEqual(h1, h2)

    def test_hash_ops_differs_on_different_input(self) -> None:
        """Different input produces different hash."""
        h1 = self.handler._hash_ops([{"name": "a"}])
        h2 = self.handler._hash_ops([{"name": "b"}])
        self.assertNotEqual(h1, h2)

    # ------------------------------------------------------------------
    # provider goneaway
    # ------------------------------------------------------------------

    def test_on_provider_goneaway_calls_callback(self) -> None:
        """Goneaway event triggers callback."""
        event = MagicMock()
        self.handler._on_provider_goneaway(event)
        self.mock_callback.assert_called_once_with(event)


if __name__ == "__main__":
    import unittest

    unittest.main()
