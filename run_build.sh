#!/bin/bash

#TODO: Add check to accept only one argument to the script

if [[ ! $1 =~ ^(keystone-k8s|glance-k8s)$ ]];
then
	echo "tox argument should be one of keystone-k8s, glance-k8s";
	exit 1
fi

pushd charms/$1
cp -rf ../../ops-sunbeam/ops_sunbeam lib/
charmcraft -v pack
./rename.sh
rm -rf lib/ops_sunbeam
popd
