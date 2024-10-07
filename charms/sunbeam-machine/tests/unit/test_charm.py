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

    PATCHES = ["sysctl"]

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
