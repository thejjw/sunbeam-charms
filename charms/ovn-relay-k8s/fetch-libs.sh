#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.observability_libs.v1.kubernetes_service_patch
charmcraft fetch-lib charms.ovn_central_k8s.v0.ovsdb
charmcraft fetch-lib charms.tls_certificates_interface.v1.tls_certificates
