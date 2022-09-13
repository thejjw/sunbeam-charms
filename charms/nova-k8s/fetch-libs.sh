#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.nginx_ingress_integrator.v0.ingress
charmcraft fetch-lib charms.sunbeam_mysql_k8s.v0.mysql
charmcraft fetch-lib charms.sunbeam_keystone_operator.v0.identity_service
charmcraft fetch-lib charms.sunbeam_rabbitmq_operator.v0.amqp
charmcraft fetch-lib charms.observability_libs.v0.kubernetes_service_patch
charmcraft fetch-lib charms.traefik_k8s.v1.ingress
charmcraft fetch-lib charms.sunbeam_nova_compute_operator.v0.cloud_compute
