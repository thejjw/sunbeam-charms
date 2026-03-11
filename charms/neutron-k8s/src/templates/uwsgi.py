#
# Copyright 2026 Canonical Ltd.
#

"""uWSGI helper template for neutron."""


def worker_id():
    """Return a stable worker identifier for uWSGI."""
    return 1


opt = {}
