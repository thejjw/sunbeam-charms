#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.keystone_k8s.v0.identity_resource
charmcraft fetch-lib charms.tls_certificates_interface.v1.tls_certificates
