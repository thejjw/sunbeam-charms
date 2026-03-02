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

"""Plain pytest tests for SunbeamMachineCharm._ensure_package_installed."""

from unittest.mock import (
    MagicMock,
)

import charm


class TestEnsurePackageInstalled:
    """Tests for package installation logic."""

    def test_when_present(self, monkeypatch):
        """Packages already installed should not trigger apt update or install."""
        mock_pkg = MagicMock()
        mock_pkg.present = True

        mock_apt = MagicMock()
        mock_apt.DebianPackage.from_system.return_value = mock_pkg
        monkeypatch.setattr(charm, "apt", mock_apt)

        charm.SunbeamMachineCharm._ensure_package_installed(MagicMock())

        mock_apt.update.assert_not_called()
        mock_pkg.ensure.assert_not_called()

    def test_when_not_present(self, monkeypatch):
        """Missing packages should trigger apt update and install."""
        mock_pkg = MagicMock()
        mock_pkg.present = False

        mock_apt = MagicMock()
        mock_apt.DebianPackage.from_system.return_value = mock_pkg
        mock_apt.PackageState.Present = "Present"
        monkeypatch.setattr(charm, "apt", mock_apt)

        mock_platform = MagicMock()
        mock_platform.release.return_value = "6.8.0-generic"
        monkeypatch.setattr(charm, "platform", mock_platform)

        charm.SunbeamMachineCharm._ensure_package_installed(MagicMock())

        mock_apt.update.assert_called_once()
        assert mock_pkg.ensure.call_count == len(charm.PACKAGES)
        mock_pkg.ensure.assert_called_with("Present")

    def test_multiple_packages_mixed_states(self, monkeypatch):
        """Only missing packages should be installed; apt.update called once."""
        mock_pkg_installed = MagicMock()
        mock_pkg_installed.present = True
        mock_pkg_missing = MagicMock()
        mock_pkg_missing.present = False

        def from_system_side_effect(package_name):
            if package_name == "open-iscsi":
                return mock_pkg_missing
            return mock_pkg_installed

        mock_apt = MagicMock()
        mock_apt.DebianPackage.from_system.side_effect = (
            from_system_side_effect
        )
        mock_apt.PackageState.Present = "Present"
        monkeypatch.setattr(charm, "apt", mock_apt)
        monkeypatch.setattr(
            charm, "PACKAGES", ["some-installed-pkg", "open-iscsi"]
        )

        charm.SunbeamMachineCharm._ensure_package_installed(MagicMock())

        mock_apt.update.assert_called_once()
        mock_pkg_installed.ensure.assert_not_called()
        mock_pkg_missing.ensure.assert_called_once_with("Present")

    def test_kernel_placeholder_substitution(self, monkeypatch):
        """Kernel placeholder in package names should be replaced."""
        installed_packages = []
        mock_pkg = MagicMock()
        mock_pkg.present = False

        def from_system_side_effect(package_name):
            installed_packages.append(package_name)
            return mock_pkg

        mock_apt = MagicMock()
        mock_apt.DebianPackage.from_system.side_effect = (
            from_system_side_effect
        )
        mock_apt.PackageState.Present = "Present"
        monkeypatch.setattr(charm, "apt", mock_apt)

        mock_platform = MagicMock()
        mock_platform.release.return_value = "5.15.0-generic"
        monkeypatch.setattr(charm, "platform", mock_platform)
        monkeypatch.setattr(
            charm, "PACKAGES", ["linux-modules-extra-{kernel}"]
        )

        charm.SunbeamMachineCharm._ensure_package_installed(MagicMock())

        assert "linux-modules-extra-5.15.0-generic" in installed_packages
        mock_apt.update.assert_called_once()
        mock_pkg.ensure.assert_called_with("Present")

    def test_mixed_kernel_and_regular_packages(self, monkeypatch):
        """Both kernel-placeholder and regular packages handled correctly."""
        installed_packages = []
        mock_pkg_present = MagicMock()
        mock_pkg_present.present = True
        mock_pkg_not_present = MagicMock()
        mock_pkg_not_present.present = False

        def from_system_side_effect(package_name):
            installed_packages.append(package_name)
            if package_name == "open-iscsi":
                return mock_pkg_not_present
            if package_name.startswith("linux-modules-extra-"):
                return mock_pkg_not_present
            return mock_pkg_present

        mock_apt = MagicMock()
        mock_apt.DebianPackage.from_system.side_effect = (
            from_system_side_effect
        )
        mock_apt.PackageState.Present = "Present"
        monkeypatch.setattr(charm, "apt", mock_apt)

        mock_platform = MagicMock()
        mock_platform.release.return_value = "6.2.0-39-generic"
        monkeypatch.setattr(charm, "platform", mock_platform)

        charm.SunbeamMachineCharm._ensure_package_installed(MagicMock())

        assert "open-iscsi" in installed_packages
        assert "linux-modules-extra-6.2.0-39-generic" in installed_packages
        assert "linux-modules-extra-{kernel}" not in installed_packages
        mock_apt.update.assert_called_once()
