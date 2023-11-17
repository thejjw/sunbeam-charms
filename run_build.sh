#!/bin/bash

if [[ ! -z $2 ]];
then
	echo "Only one argument required"
	exit 1
fi

charm=$1
charms=($(ls charms))
if [[ ! ${charms[@]} =~ $charm ]];
then
	echo "Argument should be one of ${charms[@]}";
        exit 1
fi

# Go to corresponding charm directory, copy ops_sunbeam lib, run
# charmcraft pack, rename built charm name, remove ops_sunbeam lib.
pushd charms/${charm}
cp -rf ../../ops-sunbeam/ops_sunbeam lib/
charmcraft -v pack
if [[ -e "${charm}.charm" ]];
then
    echo "Removing bad downloaded charm maybe?"
    rm "${charm}.charm"
fi
echo "Renaming charm ${charm}_*.charm to ${charm}.charm"
mv ${charm}_*.charm ${charm}.charm
rm -rf lib/ops_sunbeam
popd
