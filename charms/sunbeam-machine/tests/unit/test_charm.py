# Copyright 2024 Canonical Ltd.
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

"""Tests for Sunbeam Machine charm."""

from unittest.mock import (
    MagicMock,
    mock_open,
    patch,
)

import charm
import ops_sunbeam.test_utils as test_utils


class _SunbeamMachineCharm(charm.SunbeamMachineCharm):
    """Neutron test charm."""

    def __init__(self, framework):
        """Setup event logging."""
        self.seen_events = []
        super().__init__(framework)


class TestCharm(test_utils.CharmTestCase):
    """Classes for testing Sunbeam Machine charm."""

    PATCHES = ["sysctl", "apt"]

    def setUp(self):
        """Setup Sunbeam machine tests."""
        super().setUp(charm, self.PATCHES)
        self.harness = test_utils.get_harness(
            _SunbeamMachineCharm,
            container_calls=self.container_calls,
        )
        self.addCleanup(self.harness.cleanup)

    def test_initial(self):
        """Bootstrap test initial."""
        file_content_dict = {"PATH": "FAKEPATH"}
        env_file_content = "\n".join(
            f"{k}={v}" for k, v in file_content_dict.items()
        )

        with patch(
            "builtins.open", new_callable=mock_open, read_data=env_file_content
        ) as mock_file:
            self.harness.begin_with_initial_hooks()
            mock_file().write.assert_not_called()

        self.assertTrue(self.harness.charm.bootstrapped())

    def test_proxy_settings(self):
        """Test setting proxies."""
        # test_data is a tuple of /etc/environment file content as dict, proxy config as dict,
        # expected content as dict
        # As the below tests are run in loop as subtests, they act as juju config commands.
        # Means the configs set in the previous test data remains until it is reset by
        # specifying config as empty string.
        test_data = [
            # Case 1: No proxy in environment file, set http_proxy, https_proxy
            (
                {"PATH": "FAKEPATH"},
                {
                    "http_proxy": "http://proxyserver:3128",
                    "https_proxy": "http://proxyserver:3128",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
            ),
            # Case 2: Add no_proxy to above configuration
            (
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
                {"no_proxy": "localhost,127.0.0.1"},
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
            ),
            # Case 3: Update http proxy to different value
            (
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3128",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                {
                    "http_proxy": "http://proxyserver:3120",
                    "https_proxy": "http://proxyserver:3128",
                },
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
            ),
            # Case 4: Reset the no_proxy config
            (
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                },
                {"no_proxy": ""},
                {
                    "PATH": "FAKEPATH",
                    "HTTP_PROXY": "http://proxyserver:3120",
                    "HTTPS_PROXY": "http://proxyserver:3128",
                },
            ),
        ]

        with patch(
            "builtins.open", new_callable=mock_open, read_data=""
        ) as mock_file:
            self.harness.begin_with_initial_hooks()

        for index, d in enumerate(test_data):
            with self.subTest(msg=f"test_proxy_settings-{index}", data=d):
                env_file_content = "\n".join(
                    f"{k}={v}" for k, v in d[0].items()
                )
                expected_content = "\n".join(
                    f"{k}={v}" for k, v in d[2].items()
                )
                with patch(
                    "builtins.open",
                    new_callable=mock_open,
                    read_data=env_file_content,
                ) as mock_file:
                    self.harness.update_config(d[1])
                    mock_file().write.assert_called_with(expected_content)

    def test_ensure_package_installed_when_present(self):
        """Test package installation when packages are already installed."""
        mock_pkg = MagicMock()
        mock_pkg.present = True

        with patch(
            "charm.apt.DebianPackage.from_system", return_value=mock_pkg
        ):
            with patch("builtins.open", new_callable=mock_open, read_data=""):
                self.harness.begin_with_initial_hooks()

            # Package is present, so apt.update and ensure should not be called
            self.apt.update.assert_not_called()
            mock_pkg.ensure.assert_not_called()

    def test_ensure_package_installed_when_not_present(self):
        """Test package installation when packages need to be installed."""
        mock_pkg = MagicMock()
        mock_pkg.present = False

        with patch(
            "charm.apt.DebianPackage.from_system", return_value=mock_pkg
        ):
            with patch("charm.apt.PackageState") as mock_package_state:
                mock_package_state.Present = "Present"
                with patch(
                    "builtins.open", new_callable=mock_open, read_data=""
                ):
                    self.harness.begin_with_initial_hooks()

                # Package is not present, so apt.update should be called once
                # and ensure should be called to install the package
                self.apt.update.assert_called_once()
                mock_pkg.ensure.assert_called_once_with("Present")

    def test_ensure_package_installed_multiple_packages(self):
        """Test package installation with multiple packages in different states."""
        # Mock first package as present, second as not present
        mock_pkg1 = MagicMock()
        mock_pkg1.present = True
        mock_pkg2 = MagicMock()
        mock_pkg2.present = False

        def from_system_side_effect(package_name):
            if package_name == "open-iscsi":
                return mock_pkg2
            return mock_pkg1

        with patch(
            "charm.apt.DebianPackage.from_system",
            side_effect=from_system_side_effect,
        ):
            with patch("charm.apt.PackageState") as mock_package_state:
                mock_package_state.Present = "Present"
                # Temporarily override PACKAGES for this test
                original_packages = charm.PACKAGES
                try:
                    charm.PACKAGES = ["some-installed-pkg", "open-iscsi"]
                    with patch(
                        "builtins.open", new_callable=mock_open, read_data=""
                    ):
                        self.harness.begin_with_initial_hooks()

                    # apt.update should only be called once (not once per missing package)
                    self.apt.update.assert_called_once()
                    # Only the second package should be installed
                    mock_pkg1.ensure.assert_not_called()
                    mock_pkg2.ensure.assert_called_once_with("Present")
                finally:
                    charm.PACKAGES = original_packages
