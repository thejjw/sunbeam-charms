#!/usr/bin/env python3

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


"""Sunbeam Machine Charm.

This charm provide a place to add machine configuration and relate
subordinates that configure machine services.
"""

import logging
import platform
import socket
import textwrap
from pathlib import (
    Path,
)

import ops
import ops.framework
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.tracing as sunbeam_tracing
from charmlibs import (
    apt,
)
from charms.operator_libs_linux.v0 import (
    sysctl,
)

ETC_ENVIRONMENT = "/etc/environment"
ISCSI_INITIATORNAME_FILE = "/etc/iscsi/initiatorname.iscsi"
logger = logging.getLogger(__name__)

PACKAGES = [
    "open-iscsi",
    "linux-modules-extra-{kernel}",  # Provides nvme-tcp module
]


@sunbeam_tracing.trace_sunbeam_charm
class SunbeamMachineCharm(sunbeam_charm.OSBaseOperatorCharm):
    """Charm the service."""

    _state = ops.framework.StoredState()
    service_name = "sunbeam-machine"
    proxy_configs = ["http_proxy", "https_proxy", "no_proxy"]

    def __init__(self, framework: ops.Framework) -> None:
        super().__init__(framework)
        self.framework.observe(self.on.remove, self._on_remove)
        self.sysctl = sysctl.Config(self.meta.name)

    def configure_unit(self, event: ops.EventBase):
        """Run configuration on this unit."""
        super().configure_unit(event)
        self._sysctl_configure()
        self._ensure_package_installed()
        self._configure_iscsi_initiator()

    def _sysctl_configure(self):
        """Run sysctl configuration on the local machine."""
        sysctl_data = {"fs.inotify.max_user_instances": "1024"}
        try:
            self.sysctl.configure(config=sysctl_data)
        except (sysctl.ApplyError, sysctl.ValidationError):
            logger.error("Error setting values on sysctl", exc_info=True)
            raise sunbeam_guard.BlockedExceptionError("Sysctl config failed")
        except sysctl.CommandError:
            logger.error("Error executing sysctl", exc_info=True)
            raise sunbeam_guard.BlockedExceptionError("Sysctl command failed")

    def _configure_iscsi_initiator(self):
        """Configure the iSCSI initiator with a valid IQN."""
        fqdn = socket.getfqdn()
        iqn = f"iqn.2024-04.com.ubuntu.sunbeam:{fqdn}"
        content = textwrap.dedent(f"""\
            ## DO NOT EDIT OR REMOVE THIS FILE!
            ## This file is Juju managed.
            ## If you remove this file, the iSCSI daemon will not start.
            ## If you change the InitiatorName, existing access control lists
            ## may reject this initiator.  The InitiatorName must be unique
            ## for each iSCSI initiator.  Do NOT duplicate iSCSI InitiatorNames.
            InitiatorName={iqn}
            """)
        path = Path(ISCSI_INITIATORNAME_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch(mode=0o600)
        else:
            path.chmod(0o600)
        with path.open(mode="r+", encoding="utf-8") as file:
            data = file.read()
            if data == content:
                return
            file.seek(0)
            file.write(content)
            file.truncate()

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        self.configure_charm(event)
        with open(ETC_ENVIRONMENT, mode="r", encoding="utf-8") as file:
            current_env = dict(
                line.strip().split("=", 1) for line in file if "=" in line
            )
            logger.info(f"Existing content of /etc/environment: {current_env}")

        proxy = {p: v for p in self.proxy_configs if (v := self.config.get(p))}
        if all(
            proxy.get(p) == current_env.get(p.upper())
            for p in self.proxy_configs
        ):
            return

        # Remove proxies not set
        not_set_proxies = self.proxy_configs - proxy.keys()
        for p in not_set_proxies:
            if (p_upper := p.upper()) in current_env:
                del current_env[p_upper]

        # Capitalise proxy keys and update env
        proxy_in_caps = {k.upper(): v for k, v in proxy.items()}
        current_env.update(proxy_in_caps)

        with open(ETC_ENVIRONMENT, mode="w", encoding="utf-8") as file:
            file.write("\n".join([f"{k}={v}" for k, v in current_env.items()]))

    def _on_remove(self, event: ops.RemoveEvent):
        self.sysctl.remove()

    def _ensure_package_installed(self) -> None:
        """Ensure packages are installed on the local machine."""
        apt_updated = False
        for package in PACKAGES:
            if "{kernel}" in package:
                package = package.format(kernel=platform.release())
            pkg = apt.DebianPackage.from_system(package)
            if not pkg.present:
                if not apt_updated:
                    apt.update()
                    apt_updated = True
                pkg.ensure(apt.PackageState.Present)


if __name__ == "__main__":  # pragma: nocover
    ops.main(SunbeamMachineCharm)
