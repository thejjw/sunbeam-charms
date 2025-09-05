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
    PropertyMock,
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
        self._pk_patcher = patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "setup_private_keys",
            return_value=None,
        )
        self.addCleanup(self._pk_patcher.stop)
        self._pk_patcher.start()

        self.handler = sunbeam_rhandlers.TlsCertificatesHandler(
            charm=self.mock_charm,
            relation_name="certificates",
            callback_f=MagicMock(),
            mandatory=True,
        )
        self.handler.i_am_allowed = MagicMock(return_value=True)
        self.handler.key_names = MagicMock(return_value=["main"])
        self.handler._private_keys = {"main": "key1"}
        self.handler.csrs = MagicMock(return_value={"main": "csr1"})
        self.handler.store = MagicMock()
        self.handler.store.get_csr.return_value = "old_csr"
        self.handler.certificates = MagicMock()

    def test_renewal_flow_with_renew_true(self) -> None:
        """Test when renew=True."""
        self.handler._request_certificates(renew=True)
        self.assertTrue(
            self.handler.certificates.request_certificate_renewal.called,
            "Renewal should be triggered when renew=True",
        )

    def test_renewal_flow_with_ready_true_renew_false(self) -> None:
        """Test when renew=False and ready=True."""
        with patch.object(
            sunbeam_rhandlers.TlsCertificatesHandler,
            "ready",
            new_callable=PropertyMock,
            return_value=True,
        ):
            self.handler._request_certificates(renew=False)

        self.handler.certificates.request_certificate_renewal.assert_not_called()
        self.handler.certificates.request_certificate_creation.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
