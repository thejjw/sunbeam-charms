# Copyright (c) 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import zaza.openstack.charm_tests.test_utils as test_utils
from zaza.openstack.utilities import openstack as openstack_utils

from glanceclient.v2.client import Client as GlanceClient
import tenacity


class OpenStackImagesSyncK8sTest(test_utils.BaseCharmTest):
    """Charm tests for clusterd."""

    @classmethod
    def setUpClass(cls):
        """Run class setup for running tests."""
        super(OpenStackImagesSyncK8sTest, cls).setUpClass(
            application_name="openstack-images-sync"
        )

        keystone_session = openstack_utils.get_overcloud_keystone_session()
        cls.glance_client: GlanceClient = (
            openstack_utils.get_glance_session_client(keystone_session)
        )

    @tenacity.retry(
        wait=tenacity.wait_fixed(10),
        stop=tenacity.stop_after_delay(1800),
        reraise=True,
    )
    def _wait_for_images(self):
        """Wait for images to be downloaded."""
        images = list(self.glance_client.images.list())
        for image in images:
            if image.name.startswith("auto-sync"):
                return
        raise ValueError("No auto-sync images found")

    def test_100_check_autosync_images_downloaded(self):
        """Checking if glance as any auto-sync image."""
        try:
            self._wait_for_images()
        except ValueError as e:
            self.fail(str(e))
