#!/bin/bash

# This script generates a token that can publish the charms to charmhub.
# The token is specific to every charms inside this repository, and has to be
# generated every time a new charm is added to the repository (or when it is
# expired).

if ! command -v charmcraft &> /dev/null
then
    echo "charmcraft could not be found"
    echo "Install it with: snap install charmcraft --classic"
    exit 1
fi

if ! command -v zuul-client &> /dev/null
then
    echo "zuul-client could not be found"
    echo "Install it with: pip install zuul-client"
    exit 1
fi

opt_charm=""

for charm in $(cat charms/*/charmcraft.yaml | grep -e "^name: " | awk '{print $2}'); do
    opt_charm="$opt_charm --charm=$charm"
done

for charm in $(cat charms/storage/*/charmcraft.yaml | grep -e "^name: " | awk '{print $2}'); do
    opt_charm="$opt_charm --charm=$charm"
done


set -x
charmcraft login --export=sunbeam-charms.auth \
    $opt_charm \
    --permission=package-manage-metadata \
    --permission=package-manage-releases \
    --permission=package-manage-revisions \
    --permission=package-view-metadata \
    --permission=package-view-releases \
    --permission=package-view-revisions \
    --ttl=7776000

# Fetch project public key
[ ! -f "sunbeam-charms.pub" ] && curl  https://zuul.opendev.org/api/tenant/openstack/key/opendev.org/openstack/sunbeam-charms.pub -o sunbeam-charms.pub

zuul-client --zuul-url https://zuul.opendev.org encrypt \
  --public-key sunbeam-charms.pub \
  --tenant openstack \
  --project opendev.org/openstack/sunbeam-charms \
  --secret-name charmhub_token \
  --field-name value \
  --infile sunbeam-charms.auth \
  --outfile sunbeam-charms.charmhub.token

generated="\      # Generated on $(date --iso-8601=seconds --utc) with 90 days ttl"
sed '1d' < sunbeam-charms.charmhub.token | sed "4 i $generated" > zuul.d/secrets.yaml

# Clean the created files
rm sunbeam-charms.*
