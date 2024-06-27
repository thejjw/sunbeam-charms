#!/bin/bash

# All libraries required by sunbeam charms are centrally
# maintained in libs folder. The libraries created by
# sunbeam charms are placed in libs/internal and the
# libraries provided by external charms are maintained
# in libs/external.
# All generic template parts are maintained in
# templates/parts folder.
#
# This script provides functions for each sunbeam charms
# all the common files that should be copied to charm
# for building the charm and function testing.


NULL_ARRAY=()

# Internal libs for component. If libs are repeated, reuse the existing component
INTERNAL_CEILOMETER_LIBS=(
	"keystone_k8s"
	"ceilometer_k8s"
	"gnocchi_k8s"
)

INTERNAL_CINDER_LIBS=(
        "keystone_k8s"
        "cinder_k8s"
)

INTERNAL_CINDER_CEPH_LIBS=(
        "keystone_k8s"
        "cinder_k8s"
        "cinder_ceph_k8s"
)

INTERNAL_DESIGNATE_LIBS=(
        "keystone_k8s"
        "designate_bind_k8s"
	"designate_k8s"
)

INTERNAL_DESIGNATE_BIND_LIBS=(
        "designate_bind_k8s"
)

INTERNAL_GNOCCHI_LIBS=(
        "keystone_k8s"
        "gnocchi_k8s"
)

INTERNAL_KEYSTONE_LIBS=(
        "keystone_k8s"
)

INTERNAL_NEUTRON_LIBS=(
        "keystone_k8s"
        "ovn_central_k8s"
	"designate_k8s"
)

INTERNAL_NOVA_LIBS=(
	"keystone_k8s"
	"nova_k8s"
	"sunbeam_nova_compute_operator"
)

INTERNAL_OPENSTACK_HYPERVISOR_LIBS=(
	"keystone_k8s"
	"ovn_central_k8s"
	"cinder_ceph_k8s"
	"ceilometer_k8s"
	"nova_k8s"
)

INTERNAL_OPENSTACK_IMAGES_SYNC_LIBS=(
	"keystone_k8s"
)

INTERNAL_OVN_CENTRAL_LIBS=(
        "ovn_central_k8s"
)

# External libs for component. If libs are repeated, reuse the existing component
EXTERNAL_AODH_LIBS=(
	"data_platform_libs"
	"rabbitmq_k8s"
	"traefik_k8s"
	"certificate_transfer_interface"
)

EXTERNAL_BARBICAN_LIBS=(
        "data_platform_libs"
        "rabbitmq_k8s"
        "traefik_k8s"
	"vault_k8s"
	"certificate_transfer_interface"
)

EXTERNAL_CEILOMETER_LIBS=(
        "rabbitmq_k8s"
	"certificate_transfer_interface"
)

EXTERNAL_CINDER_CEPH_LIBS=(
        "data_platform_libs"
        "rabbitmq_k8s"
        "traefik_k8s"
)

EXTERNAL_DESIGNATE_BIND_LIBS=(
	"observability_libs"
)

EXTERNAL_HEAT_LIBS=(
        "data_platform_libs"
        "rabbitmq_k8s"
        "traefik_route_k8s"
	"certificate_transfer_interface"
)

EXTERNAL_NEUTRON_LIBS=(
        "data_platform_libs"
        "rabbitmq_k8s"
        "traefik_k8s"
	"tls_certificates_interface"
	"certificate_transfer_interface"
)

EXTERNAL_NOVA_LIBS=(
        "data_platform_libs"
        "rabbitmq_k8s"
        "traefik_k8s"
        "traefik_route_k8s"
        "certificate_transfer_interface"
)

EXTERNAL_OCTAVIA_LIBS=(
        "data_platform_libs"
        "traefik_k8s"
        "tls_certificates_interface"
	"certificate_transfer_interface"
)

EXTERNAL_OPENSTACK_EXPORTER_LIBS=(
        "grafana_k8s"
        "prometheus_k8s"
        "tls_certificates_interface"
	"certificate_transfer_interface"
)

EXTERNAL_OPENSTACK_HYPERVISOR_LIBS=(
	"data_platform_libs"
        "grafana_agent"
	"observability_libs"
	"operator_libs_linux"
	"rabbitmq_k8s"
        "traefik_k8s"
        "tls_certificates_interface"
	"certificate_transfer_interface"
)

EXTERNAL_OPENSTACK_IMAGES_SYNC_LIBS=(
    "traefik_k8s"
)

EXTERNAL_SUNBEAM_CLUSTERD_LIBS=(
	"operator_libs_linux"
	"tls_certificates_interface"
)

EXTERNAL_SUNBEAM_MACHINE_LIBS=(
	"operator_libs_linux"
)

EXTERNAL_OVN_CENTRAL_LIBS=(
        "tls_certificates_interface"
)

EXTERNAL_OVN_RELAY_LIBS=(
        "tls_certificates_interface"
	"observability_libs"
)

EXTERNAL_TEMPEST_LIBS=(
	"observability_libs"
        "grafana_k8s"
        "loki_k8s"
)

# Config template parts for each component.
CONFIG_TEMPLATES_AODH=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-credentials"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_BARBICAN=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_CEILOMETER=(
	"parts/identity-data-id-creds"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-credentials-from-identity-service"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_CINDER=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_CINDER_CEPH=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-oslo-notifications"
)

CONFIG_TEMPLATES_DESIGNATE=(
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_GLANCE=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-oslo-notifications"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_GNOCCHI=(
	"parts/database-connection"
	"parts/section-identity"
	"parts/identity-data"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_HEAT=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-trustee"
	"parts/section-oslo-messaging-rabbit"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_HORIZON=(
        "ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_KEYSTONE=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-federation"
	"parts/section-middleware"
	"parts/section-oslo-cache"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-oslo-middleware"
	"parts/section-oslo-notifications"
	"parts/section-signing"
)

CONFIG_TEMPLATES_MAGNUM=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-user"
	"parts/section-trust"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_NEUTRON=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-oslo-messaging-rabbit"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_NOVA=${CONFIG_TEMPLATES_NEUTRON[@]}

CONFIG_TEMPLATES_OCTAVIA=(
	"parts/section-database"
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"ca-bundle.pem.j2"
)

CONFIG_TEMPLATES_PLACEMENT=(
	"parts/database-connection"
	"parts/database-connection-settings"
	"parts/section-identity"
	"parts/identity-data"
	"parts/section-service-user"
	"ca-bundle.pem.j2"
)

declare -A INTERNAL_LIBS=(
	[aodh-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[barbican-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[ceilometer-k8s]=${INTERNAL_CEILOMETER_LIBS[@]}
	[cinder-k8s]=${INTERNAL_CINDER_LIBS[@]}
        [cinder-ceph-k8s]=${INTERNAL_CINDER_CEPH_LIBS[@]}
	[designate-k8s]=${INTERNAL_DESIGNATE_LIBS[@]}
	[designate-bind-k8s]=${INTERNAL_DESIGNATE_BIND_LIBS[@]}
	[glance-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[gnocchi-k8s]=${INTERNAL_GNOCCHI_LIBS[@]}
	[heat-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[horizon-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
        [keystone-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[keystone-ldap-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[magnum-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[neutron-k8s]=${INTERNAL_NEUTRON_LIBS[@]}
	[nova-k8s]=${INTERNAL_NOVA_LIBS[@]}
	[octavia-k8s]=${INTERNAL_NEUTRON_LIBS[@]}
	[openstack-exporter-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[openstack-hypervisor]=${INTERNAL_OPENSTACK_HYPERVISOR_LIBS[@]}
	[openstack-images-sync-k8s]=${INTERNAL_OPENSTACK_IMAGES_SYNC_LIBS[@]}
	[sunbeam-clusterd]=${NULL_ARRAY[@]}
	[sunbeam-machine]=${NULL_ARRAY[@]}
	[ovn-central-k8s]=${INTERNAL_OVN_CENTRAL_LIBS[@]}
        [ovn-relay-k8s]=${INTERNAL_OVN_CENTRAL_LIBS[@]}
	[placement-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
	[tempest-k8s]=${INTERNAL_KEYSTONE_LIBS[@]}
)

declare -A EXTERNAL_LIBS=(
        [aodh-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [barbican-k8s]=${EXTERNAL_BARBICAN_LIBS[@]}
        [ceilometer-k8s]=${EXTERNAL_CEILOMETER_LIBS[@]}
        [cinder-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [cinder-ceph-k8s]=${EXTERNAL_CINDER_CEPH_LIBS[@]}
        [designate-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [designate-bind-k8s]=${EXTERNAL_DESIGNATE_BIND_LIBS[@]}
        [glance-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [gnocchi-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [heat-k8s]=${EXTERNAL_HEAT_LIBS[@]}
        [horizon-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [keystone-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [keystone-ldap-k8s]=${NULL_ARRAY[@]}
        [magnum-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [neutron-k8s]=${EXTERNAL_NEUTRON_LIBS[@]}
        [nova-k8s]=${EXTERNAL_NOVA_LIBS[@]}
        [octavia-k8s]=${EXTERNAL_OCTAVIA_LIBS[@]}
        [openstack-exporter-k8s]=${EXTERNAL_OPENSTACK_EXPORTER_LIBS[@]}
        [openstack-hypervisor]=${EXTERNAL_OPENSTACK_HYPERVISOR_LIBS[@]}
        [openstack-images-sync-k8s]=${EXTERNAL_OPENSTACK_IMAGES_SYNC_LIBS[@]}
	[sunbeam-clusterd]=${EXTERNAL_SUNBEAM_CLUSTERD_LIBS[@]}
	[sunbeam-machine]=${EXTERNAL_SUNBEAM_MACHINE_LIBS[@]}
	[ovn-central-k8s]=${EXTERNAL_OVN_CENTRAL_LIBS[@]}
        [ovn-relay-k8s]=${EXTERNAL_OVN_RELAY_LIBS[@]}
        [placement-k8s]=${EXTERNAL_AODH_LIBS[@]}
        [tempest-k8s]=${EXTERNAL_TEMPEST_LIBS[@]}
)

declare -A CONFIG_TEMPLATES=(
        [aodh-k8s]=${CONFIG_TEMPLATES_AODH[@]}
        [barbican-k8s]=${CONFIG_TEMPLATES_BARBICAN[@]}
        [ceilometer-k8s]=${CONFIG_TEMPLATES_CEILOMETER[@]}
        [cinder-k8s]=${CONFIG_TEMPLATES_CINDER[@]}
        [cinder-ceph-k8s]=${CONFIG_TEMPLATES_CINDER_CEPH[@]}
        [designate-k8s]=${CONFIG_TEMPLATES_DESIGNATE[@]}
        [designate-bind-k8s]=${NULL_ARRAY[@]}
        [glance-k8s]=${CONFIG_TEMPLATES_GLANCE[@]}
	[gnocchi-k8s]=${CONFIG_TEMPLATES_GNOCCHI[@]}
        [heat-k8s]=${CONFIG_TEMPLATES_HEAT[@]}
        [horizon-k8s]=${CONFIG_TEMPLATES_HORIZON[@]}
        [keystone-k8s]=${CONFIG_TEMPLATES_KEYSTONE[@]}
        [keystone-ldap-k8s]=${NULL_ARRAY[@]}
        [magnum-k8s]=${CONFIG_TEMPLATES_MAGNUM[@]}
        [neutron-k8s]=${CONFIG_TEMPLATES_NEUTRON[@]}
        [nova-k8s]=${CONFIG_TEMPLATES_NOVA[@]}
        [octavia-k8s]=${CONFIG_TEMPLATES_OCTAVIA[@]}
        [openstack-exporter-k8s]=${CONFIG_TEMPLATES_HORIZON[@]}
        [openstack-hypervisor]=${NULL_ARRAY[@]}
        [openstack-images-sync-k8s]=${NULL_ARRAY[@]}
        [sunbeam-clusterd]=${NULL_ARRAY[@]}
        [sunbeam-machine]=${NULL_ARRAY[@]}
        [ovn-central-k8s]=${NULL_ARRAY[@]}
        [ovn-relay-k8s]=${NULL_ARRAY[@]}
        [placement-k8s]=${CONFIG_TEMPLATES_PLACEMENT[@]}
        [tempest-k8s]=${NULL_ARRAY[@]}
)


function copy_ops_sunbeam {
	cp -rf ../../ops-sunbeam/ops_sunbeam lib/
}

function copy_internal_libs {
	internal_libs_=${INTERNAL_LIBS[$1]}
	echo "copy_internal_libs for $1:"
	for lib in ${internal_libs_[@]}; do
		echo "Copying $lib"
                cp -rf ../../libs/internal/lib/charms/$lib lib/charms/
        done
}

function copy_external_libs {
	echo "copy_external_libs for $1:"
	external_libs_=${EXTERNAL_LIBS[$1]}
	for lib in ${external_libs_[@]}; do
		echo "Copying $lib"
                cp -rf ../../libs/external/lib/charms/$lib lib/charms/
        done
}

function copy_config_templates {
	echo "copy_config_templates for $1:"
	config_templates_=${CONFIG_TEMPLATES[$1]}
	for part in ${config_templates_[@]}; do
		echo "Copying $part"
                cp -rf ../../templates/$part src/templates/$part
        done
}

function copy_juju_ignore {
        cp ../../.jujuignore .
}

function copy_stestr_conf {
	cp ../../.stestr.conf .
}

function remove_libs {
	rm -rf lib
}

function remove_config_templates {
	echo "remove_config_templates for $1:"
        config_templates_=${CONFIG_TEMPLATES[$1]}
        for part in ${config_templates_[@]}; do
                echo "Removing $part"
                rm src/templates/$part
        done

	if (test -d src/templates/parts) && (test -n "$(find src/templates/parts -maxdepth 0 -empty)")
	then
		remove_templates_parts_dir
	fi
}

function remove_templates_parts_dir {
	rm -rf src/templates/parts
}

function remove_juju_ignore {
	rm .jujuignore
}

function remove_stestr_conf {
	rm .stestr.conf
}

function push_common_files {
	if [[ $# != 1 ]];
	then
		echo "push_common_files: Expected one argument"
		exit 1
	fi

	pushd charms/$1

        mkdir -p lib/charms
        mkdir -p src/templates/parts

        copy_ops_sunbeam
	copy_internal_libs $1
	copy_external_libs $1
	copy_config_templates $1
	copy_stestr_conf
	copy_juju_ignore

	popd
}

function pop_common_files {
	pushd charms/$1

	remove_libs
	remove_config_templates $1
	remove_stestr_conf
        remove_juju_ignore

	popd
}

function copy_libs_for_ops_sunbeam {
	mkdir -p tests/lib
        cp -rf ../libs/external/lib ../libs/internal/lib tests/
}

function remove_libs_for_ops_sunbeam {
	rm -rf tests/lib
}
