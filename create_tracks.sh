#!/bin/bash

# This script look inside publish_channels for all tracks that should exits.
# If the track does not exist, it creates it.

if ! command -v charmcraft &> /dev/null
then
    echo "charmcraft could not be found"
    echo "Install it with: snap install charmcraft --classic"
    exit 1
fi

if ! command -v yq &> /dev/null
then
    echo "yq could not be found"
    echo "Install it with: snap install yq"
    exit 1
fi

if ! command -v jq &> /dev/null
then
    echo "jq could not be found"
    echo "Install it with: snap install jq"
    exit 1
fi

if ! command -v curl &> /dev/null
then
    echo "curl could not be found"
    echo "Install it with: apt install curl"
    exit 1
fi

opt_charm=""

publish_channel=$(yq -r '.[0].project.vars.publish_channels | to_entries | .[] | (.key + " " + .value)' zuul.d/zuul.yaml)

while IFS=' ' read -r charm _; do
    opt_charm="$opt_charm --charm=$charm"
done < <(echo "$publish_channel")


charmcraft login --export=sunbeam-charms.auth \
    $opt_charm \
    --permission=package-view-metadata \
    --permission=package-manage-metadata \
    --ttl=3600

CHARMHUB_MACAROON_HEADER="Authorization: Macaroon $(cat sunbeam-charms.auth | base64 -d | jq -r .v)"

while IFS=' ' read -r charm channel; do
    track=$(echo "$channel" | cut -d'/' -f1)
    url=https://api.charmhub.io/v1/charm/$charm
    metadata=$(curl -s "$url" -H'Content-type: application/json' -H "$CHARMHUB_MACAROON_HEADER")
    tracks=$(echo "$metadata" | jq -r '.metadata.tracks[].name')
    if [[ $tracks =~ $track ]]; then
        echo "Track $track already exists for charm $charm"
    else
        echo "Creating track $track for charm $charm"
    curl -s "$url/tracks" -X POST -H'Content-type: application/json' -H "$CHARMHUB_MACAROON_HEADER" -d '[{"name": "'"$track"'"}]'
    fi
done < <(echo "$publish_channel")

# Clean the created files
rm sunbeam-charms.*
