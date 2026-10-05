#
# Copyright 2026 Canonical Ltd.
#

"""uWSGI helper template for neutron."""

opt = {}

# Consumed by neutron.common.wsgi_utils.get_api_worker_count(), set to the
# number of WSGIDaemonProcess processes in wsgi-neutron-api.conf.j2
numproc = 4
