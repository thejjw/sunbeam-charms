#!/usr/bin/env bash

set -o xtrace

source common.sh

if [[ $1 == "fmt" ]];
then
	src_path_array=$(ls -d -1 "charms/"**/src)
        tst_path_array=$(ls -d -1 "charms/"**/tests)
        lib_path_array=$(ls -d -1 "charms/"**/lib)

        src_path="${src_path_array[*]}"
        tst_path="${tst_path_array[*]}"
        lib_path="${lib_path_array[*]}"

	isort ${src_path} ${tst_path}
	black --config pyproject.toml ${src_path} ${tst_path}
elif [[ $1 == "pep8" ]];
then
	src_path_array=$(ls -d -1 "charms/"**/src)
	tst_path_array=$(ls -d -1 "charms/"**/tests)
	
	src_path="${src_path_array[*]}"
	tst_path="${tst_path_array[*]}"

	codespell ${src_path} ${tst_path}
        pflake8 --config pyproject.toml ${src_path} ${tst_path}
	isort --check-only --diff ${src_path} ${tst_path}
	black --config pyproject.toml --check --diff ${src_path} ${tst_path}
elif [[ $1 =~ ^(py3|py310|py311)$ ]];
then
	# Run py3 on ops-sunbeam
	pushd ops-sunbeam
        stestr run --slowest || exit 1
        popd

	# Run py3 on all sunbeam charms
	charms=($(ls charms))
	for charm in ${charms[@]}; do
		push_common_files $charm || exit 1
		pushd charms/$charm
		PYTHONPATH=./src:./lib stestr run --slowest || exit 1
		popd
		pop_common_files $charm || exit 1
	done
elif [[ $1 == "cover" ]];
then
        coverage erase

	# Run coverage on ops-sunbeam
	pushd ops-sunbeam
	coverage erase
        PYTHON="coverage run --omit .tox/*,tests/*" stestr run --slowest || exit 1
	coverage combine
        popd

	# Run coverage on all sunbeam charms
	charms=($(ls charms))
        for charm in ${charms[@]}; do
		push_common_files $charm || exit 1
                pushd charms/$charm
		coverage erase
                PYTHONPATH=./src:./lib:../../ops-sunbeam PYTHON="coverage run --omit .tox/*,tests/*,src/templates/*" stestr run --slowest || exit 1
		coverage combine
                popd
        done

	# Prepare coverage report
	coverage combine charms/*/.coverage ops-sunbeam/.coverage
	coverage html -d cover
	coverage xml -o cover/coverage.xml
	coverage report

	# Common files should be deleted after coverage combine
	for charm in ${charms[@]}; do
		pop_common_files $charm || exit 1
	done
elif [[ $1 == "build" ]];
then
	if [[ $# != 2 ]];
	then
		echo "Command format: tox -e build <charm>"
		exit 1
	fi

	charm="$2"
	charms=($(ls charms))
	if [[ ! ${charms[@]} =~ $charm ]];
	then
		echo "Argument should be one of ${charms[@]}";
		exit 1
	fi

	push_common_files $charm || exit 1
	cd "charms/$charm"


	# getent hosts snapcraft.io
	# sysctl net.ipv4.ip_forward
	# sysctl net.ipv6.conf.all.forwarding
	systemctl --type=service
	sudo iptables -L
	sudo iptables -L -t nat
	sudo iptables -S
	sudo iptables-save
	# # sudo iptables -D openstack-INPUT -j REJECT --reject-with icmp-host-prohibited
	# # sudo iptables-save
	# sudo ufw allow in on lxdbr0
	# sudo ufw route allow in on lxdbr0
	# sudo ufw route allow out on lxdbr0
	# lxc network list
	# lxc network show lxdbr0 || true
	# lxc profile list
	# lxc profile show default || true
	# ip a
	# lxc info | grep 'firewall:'
	# sudo lsof -n -i :53
	# pgrep dnsmasq
	# sudo ss -ulpn
	# lxc launch ubuntu:22.04 test
	# lxc exec test -- ip a
	# lxc exec test -- ping snapcraft.io
	# lxc config show test
	# lxc rm -f test
	charmcraft --verbosity trace pack
	if [[ -e "${charm}.charm" ]];
	then
		echo "Removing bad downloaded charm maybe?"
		rm "${charm}.charm"
	fi
	echo "Renaming charm ${charm}_ubuntu-22.04-amd64.charm to ${charm}.charm"

	# fix when building for other targets than amd64
	mv ${charm}_ubuntu-22.04-amd64.charm ${charm}.charm

	cd -
	pop_common_files $charm || exit 1
else
	echo "tox argument should be one of pep8, py3, py310, py311, cover";
        exit 1
fi
