#!/bin/bash

#TODO: Add check to accept only one argument to the script

if [[ ! $1 =~ ^(keystone-k8s|placement-k8s)$ ]];
then
	echo "tox argument should be one of keystone-k8s, placement-k8s";
	exit 1
fi

charm=$1

pushd charms/${charm}
cp -rf ../../ops-sunbeam/ops_sunbeam lib/
charmcraft -v pack
if [[ -e "${charm}.charm" ]];
then
    echo "Removing bad downloaded charm maybe?"
    rm "${charm}.charm"
fi
echo "Renaming charm to ${charm}.charm"
mv ${charm}_*.charm ${charm}.charm
rm -rf lib/ops_sunbeam
popd
