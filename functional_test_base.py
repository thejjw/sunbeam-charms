"""Select the base used by Sunbeam's functional tests."""

import os

DEFAULT_FUNCTIONAL_TEST_BASE = "ubuntu@26.04"


def get_functional_test_base() -> str:
    """Use the requested base or the release's default."""
    return os.environ.get("SUNBEAM_TEST_BASE", DEFAULT_FUNCTIONAL_TEST_BASE)
