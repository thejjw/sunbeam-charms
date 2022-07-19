#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.observability_libs.v0.kubernetes_service_patch
charmcraft fetch-lib charms.sunbeam_ovn_central_operator.v0.ovsdb
