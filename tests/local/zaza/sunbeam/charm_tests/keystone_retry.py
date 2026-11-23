# Copyright (c) 2026 Canonical Ltd.
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

"""Keystone helpers that retry calls that fail temporarily.

The CI node is small. It can fail keystone calls for a short time: a 5xx
response, or a refused or timed out TCP connection. Zaza does not retry
these calls. One temporary failure stops the whole test step.

This module holds one retry policy:

* ``keystone_call_with_retry`` retries one keystone call.
* The function ``_patch_zaza_session_factory`` adds the same retry
  policy to the keystone session factory of zaza. The patch covers zaza
  code that makes its own session (for example, the glance setup in
  zaza).
"""

import keystoneauth1.exceptions as kse
import tenacity
from zaza.openstack.utilities import openstack as openstack_utils

KEYSTONE_RETRIES = 5
KEYSTONE_RETRY_DELAY = 5

RETRYABLE_EXCEPTIONS = (
    kse.http.ServiceUnavailable,
    kse.http.InternalServerError,
    kse.http.BadGateway,
    kse.http.GatewayTimeout,
    kse.connection.ConnectFailure,
    kse.connection.ConnectTimeout,
)


def keystone_call_with_retry(fn, *args, **kwargs):
    """Run one keystone call. Retry it when it fails temporarily.

    For a sequence of create calls that is not idempotent, retry each
    call separately. Do not wrap the whole sequence.

    :param fn: The callable to run.
    :param args: Positional arguments for fn.
    :param kwargs: Keyword arguments for fn.
    :returns: The return value of fn.
    """
    for attempt in tenacity.Retrying(
        stop=tenacity.stop_after_attempt(KEYSTONE_RETRIES),
        wait=tenacity.wait_fixed(KEYSTONE_RETRY_DELAY),
        retry=tenacity.retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    ):
        with attempt:
            return fn(*args, **kwargs)


def _patch_zaza_session_factory():
    """Add the retry policy to the keystone session factory of zaza.

    Some zaza code makes its own session through
    ``get_overcloud_keystone_session``. This code cannot call
    ``keystone_call_with_retry``. The patch covers this code too.
    zaza reads the name of the function when it calls it. So the patch
    also covers modules that are already imported.

    The original function is stored under a private attribute. When the
    module is imported a second time, the patch uses the original
    function again. The patch does not wrap itself.
    """
    original = getattr(
        openstack_utils,
        "_sunbeam_original_get_overcloud_keystone_session",
        openstack_utils.get_overcloud_keystone_session,
    )

    def _patched(*args, **kwargs):
        return keystone_call_with_retry(lambda: original(*args, **kwargs))

    openstack_utils._sunbeam_original_get_overcloud_keystone_session = original
    openstack_utils.get_overcloud_keystone_session = _patched


_patch_zaza_session_factory()
