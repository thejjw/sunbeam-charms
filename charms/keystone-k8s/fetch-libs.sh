#!/bin/bash

echo "INFO: Fetching libs from charmhub."
charmcraft fetch-lib charms.nginx_ingress_integrator.v0.ingress
charmcraft fetch-lib charms.data_platform_libs.v0.database_requires
#charmcraft fetch-lib charms.sunbeam_keystone_operator.v1.identity_service
#charmcraft fetch-lib charms.sunbeam_keystone_operator.v0.identity_credentials
charmcraft fetch-lib charms.sunbeam_rabbitmq_operator.v0.amqp
charmcraft fetch-lib charms.traefik_k8s.v1.ingress
