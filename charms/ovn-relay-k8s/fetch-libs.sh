#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.observability_libs.v0.kubernetes_service_patch
charmcraft fetch-lib charms.ovn_central_k8s.v0.ovsdb
