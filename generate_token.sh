#!/bin/bash

# This script generates a token that can publish the charms to charmhub.
# The token is specific to every charms inside this repository, and has to be
# generated every time a new charm is added to the repository (or when it is
# expired).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v charmcraft &> /dev/null
then
    echo "charmcraft could not be found"
    echo "Install it with: snap install charmcraft --classic"
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

"${SCRIPT_DIR}/encrypt_secret.sh" \
    --secret-name charmhub_token \
    --field-name value \
    --infile sunbeam-charms.auth \
    --generated "with 90 days ttl"

# Clean the created files
rm sunbeam-charms.auth
