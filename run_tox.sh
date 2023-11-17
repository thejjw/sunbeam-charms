#!/bin/bash

if [[ ! $1 =~ ^(pep8|py3|py310|py311|cover)$ ]];
then
	echo "tox argument should be one of pep8, py3, py310, py311, cover";
	exit 1
fi

# Check if ops-sunbeam is changed. If so run tox on all charms.
# Otherwise, run tox on only the charms whose code is modifed.
ops_sunbeam_changed=$(git diff --name-only --relative=ops-sunbeam)
if [ -z ops_sunbeam_changed ];
then
	modified_charms=$(git diff --name-only --relative=charms | sed -r "s/([^/]*).+/\1/" | sort -u)
else
	modified_charms=$(ls charms)
fi

# In Opendev zuul, the tox is available @ {{ ansible_user_dir }}/.local/tox/bin/tox
# use above binary if it exists or use just tox
tox_executable="tox"
if test -f $HOME/.local/tox/bin/tox;
then
	tox_executable=$HOME/.local/tox/bin/tox
fi

for charm in $modified_charms; do pushd charms/$charm; $tox_executable -e $1 || exit 1; popd; done
