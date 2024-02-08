#!/usr/bin/env bash

set -o xtrace

source common.sh

# print checks to test based on the first arg
get_charms_to_test() {
    local charm=$1
    if [[ -z "$charm" ]]; then
        ls charms
    elif [[ "$charm" = "ops-sunbeam" ]]; then
        # ops-sunbeam is treated differently, so don't process it here
        false
    else
        local charms=($(ls charms))
        if [[ ! ${charms[@]} =~ $charm ]];
        then
            echo "Argument should be one of ${charms[@]}" >&2
            exit 1
        fi
        echo "$charm"
    fi
}

should_test_ops_sunbeam() {
    # ops-sunbeam should be tested
    # if no arguments (test everything)
    # or ops-sunbeam is specified.
    if [[ -z "$1" || "$1" = "ops-sunbeam" ]]; then
        true
    else
        false
    fi
}

if [[ $1 == "fmt" ]];
then
    src_path_array=$(ls -d -1 "charms/"**/src)
    tst_path_array=$(ls -d -1 "charms/"**/tests)
    lib_path_array=$(ls -d -1 "charms/"**/lib)
    
    src_path="${src_path_array[*]}"
    tst_path="${tst_path_array[*]}"
    lib_path="${lib_path_array[*]}"

    ops_sunbeam_src_path="ops-sunbeam/ops_sunbeam"
    ops_sunbeam_tst_path="ops-sunbeam/tests/unit_tests"

    isort ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
    black --config pyproject.toml ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
elif [[ $1 == "pep8" ]];
then
    src_path_array=$(ls -d -1 "charms/"**/src)
    tst_path_array=$(ls -d -1 "charms/"**/tests)

    src_path="${src_path_array[*]}"
    tst_path="${tst_path_array[*]}"

    ops_sunbeam_src_path="ops-sunbeam/ops_sunbeam"
    ops_sunbeam_tst_path="ops-sunbeam/tests/unit_tests"

    codespell ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
    pflake8 --config pyproject.toml ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
    isort --check-only --diff ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
    black --config pyproject.toml --check --diff ${src_path} ${tst_path} ${ops_sunbeam_src_path} ${ops_sunbeam_tst_path}
elif [[ $1 =~ ^(py3|py310|py311)$ ]];
then

    # Run py3 on ops-sunbeam
    if should_test_ops_sunbeam $2; then
        pushd ops-sunbeam
        stestr run --slowest || exit 1
        popd
    fi

    # Run py3 on sunbeam charms
    for charm in $(get_charms_to_test $2); do
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
    if should_test_ops_sunbeam $2; then
        pushd ops-sunbeam
        coverage erase
        PYTHON="coverage run --parallel-mode --omit .tox/*" stestr run --slowest || exit 1
        coverage combine
        popd
    fi

    # Run coverage on sunbeam charms
    for charm in $(get_charms_to_test $2); do
        push_common_files $charm || exit 1
        pushd charms/$charm
        coverage erase
        PYTHONPATH=./src:./lib:../../ops-sunbeam PYTHON="coverage run --parallel-mode --omit .tox/*,src/templates/*" stestr run --slowest || exit 1
        coverage combine
        popd
    done

    coverage_files=( charms/*/.coverage ops-sunbeam/.coverage )
    existing_coverage_files=()
    for file in "${coverage_files[@]}"; do
        [[ -e $file ]] && existing_coverage_files+=( $file )
    done
    # Prepare coverage report
    coverage combine "${existing_coverage_files[@]}"
    coverage html -d cover
    coverage xml -o cover/coverage.xml
    coverage report

    # Common files should be deleted after coverage combine
    for charm in $(get_charms_to_test $2); do
        pop_common_files $charm || exit 1
    done

elif [[ $1 == "build" ]];
then
    if [[ $# != 2 ]];
    then
        echo "Command format: tox -e build <charm>"
        exit 1
    fi

    charm=$2
    charms=($(ls charms))
    if [[ ! ${charms[@]} =~ $charm ]];
    then
        echo "Argument should be one of ${charms[@]}";
        exit 1
    fi

    push_common_files $charm || exit 1
    pushd charms/$charm || exit 1
    charmcraft -v pack || exit 1
    if [[ -e "${charm}.charm" ]];
    then
        echo "Removing bad downloaded charm maybe?"
        rm "${charm}.charm"
    fi
    echo "Renaming charm ${charm}_*.charm to ${charm}.charm"

    mv ${charm}_*.charm ${charm}.charm

    popd || exit 1
    pop_common_files $charm || exit 1
else
    echo "tox argument should be one of pep8, py3, py310, py311, cover";
        exit 1
fi
