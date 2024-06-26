#!/bin/bash

pushd libs/external

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.certificate_transfer_interface.v0.certificate_transfer
charmcraft fetch-lib charms.data_platform_libs.v0.data_interfaces
charmcraft fetch-lib charms.grafana_k8s.v0.grafana_dashboard
charmcraft fetch-lib charms.grafana_agent.v0.cos_agent
charmcraft fetch-lib charms.loki_k8s.v1.loki_push_api
charmcraft fetch-lib charms.observability_libs.v0.juju_topology
charmcraft fetch-lib charms.observability_libs.v1.kubernetes_service_patch
charmcraft fetch-lib charms.operator_libs_linux.v0.sysctl
charmcraft fetch-lib charms.operator_libs_linux.v2.snap
charmcraft fetch-lib charms.prometheus_k8s.v0.prometheus_scrape
charmcraft fetch-lib charms.rabbitmq_k8s.v0.rabbitmq
charmcraft fetch-lib charms.tls_certificates_interface.v3.tls_certificates
charmcraft fetch-lib charms.traefik_k8s.v2.ingress
charmcraft fetch-lib charms.traefik_route_k8s.v0.traefik_route
charmcraft fetch-lib charms.vault_k8s.v0.vault_kv

popd
