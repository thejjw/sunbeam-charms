import pathlib
import re
import subprocess
import sys

import pytest
import yaml


ROOT = pathlib.Path(__file__).parents[1]
EXCLUDED_CHARM_PATHS = {"sunbeam-libs"}


def _load_yaml(path):
    with (ROOT / path).open() as fh:
        return yaml.safe_load(fh)


def _jobs():
    return {
        entry["job"]["name"]: entry["job"]
        for entry in _load_yaml("zuul.d/jobs.yaml")
        if "job" in entry
    }


def _project_template(name):
    for entry in _load_yaml("zuul.d/project-templates.yaml"):
        template = entry.get("project-template", {})
        if template.get("name") == name:
            return template
    raise AssertionError(f"project-template {name} not found")


def _pipeline_jobs(template, pipeline):
    names = []
    for item in template[pipeline]["jobs"]:
        names.append(next(iter(item)) if isinstance(item, dict) else item)
    return names


def _charmcraft_paths():
    charm_paths = {
        str(path.parent.relative_to(ROOT / "charms"))
        for path in (ROOT / "charms").glob("**/charmcraft.yaml")
    }
    return sorted(charm_paths - EXCLUDED_CHARM_PATHS)


def _bin_jobs(jobs):
    return {
        name: job
        for name, job in jobs.items()
        if job.get("parent") == "charm-build-bin"
    }


def _bin_charm_builds(bin_jobs):
    return [
        build
        for job in bin_jobs.values()
        for build in job["vars"]["charm_builds"]
    ]


def test_bin_jobs_cover_buildable_charms_once():
    builds = _bin_charm_builds(_bin_jobs(_jobs()))
    charm_paths = [build["path"] for build in builds]

    assert sorted(charm_paths) == _charmcraft_paths()
    assert len(charm_paths) == len(set(charm_paths))


def test_bin_jobs_have_expected_files_and_build_metadata():
    for job_name, job in _bin_jobs(_jobs()).items():
        assert job_name.startswith("charm-build-bin-")
        assert job["files"][0] == "ops-sunbeam/ops_sunbeam/"
        assert "^rebuild$" in job["files"]

        for build in job["vars"]["charm_builds"]:
            charm_path = pathlib.Path(build["path"])
            assert build["name"] == charm_path.name
            assert f"charms/{build['path']}/" in job["files"]
            assert (ROOT / "charms" / build["path"] / "charmcraft.yaml").exists()


def test_build_template_uses_only_concrete_bin_jobs():
    jobs = _jobs()
    template = _project_template("openstack-sunbeam-charm-build-jobs")
    expected_jobs = [
        name
        for parent in _bin_jobs(jobs)
        for name in (parent, f"{parent}-noble")
    ]

    assert _pipeline_jobs(template, "check") == expected_jobs
    assert _pipeline_jobs(template, "gate") == expected_jobs


def test_charm_build_jobs_are_only_bins():
    jobs = _jobs()
    charm_build_jobs = [
        name
        for name in jobs
        if name.startswith("charm-build-") and name != "charm-build-bin"
    ]

    bins = _bin_jobs(jobs)
    assert set(charm_build_jobs) == set(bins) | {f"{name}-noble" for name in bins}
    for name in bins:
        sibling = jobs[f"{name}-noble"]
        assert sibling["parent"] == name
        assert sibling["vars"]["charm_build_platform"] == "noble-amd64"
    assert jobs["charm-build-bin"]["vars"]["charm_build_platform"] == "resolute-amd64"


def test_functional_jobs_depend_on_matching_bins_for_artifact_downloads():
    jobs = _jobs()
    primary_bins = set(_bin_jobs(jobs))
    project = _load_yaml("zuul.d/zuul.yaml")[0]["project"]
    checks = {entry for entry in project["check"]["jobs"] if isinstance(entry, str)}
    base_template = _project_template("openstack-sunbeam-charm-build-jobs")
    check_overrides = {
        name: config
        for entry in base_template["check"]["jobs"]
        if isinstance(entry, dict)
        for name, config in entry.items()
    }

    assert checks
    assert project["vars"]["func_charm_base"] == "ubuntu@26.04"
    for name in checks:
        job = jobs[name]
        noble = name.endswith("-noble")
        primary = jobs[job["parent"]] if noble else job
        playbook = primary["run"]
        assert any(re.match(pattern, playbook) for pattern in primary["files"]), name
        dependencies = {dep["name"] for dep in job["dependencies"]}
        assert set(job["vars"]["charm_jobs"]) == dependencies
        assert all(not dep.get("soft") for dep in job["dependencies"])
        if noble:
            assert job["nodeset"] == "charms-ubuntu-noble-32GB"
            assert job["vars"]["func_charm_base"] == "ubuntu@24.04"
            assert job["parent"] in checks
            assert dependencies == {f"{bin_name}-noble" for bin_name in primary["vars"]["charm_jobs"]}
        else:
            assert job["nodeset"] == "charms-ubuntu-resolute-32GB"
            assert dependencies <= primary_bins
            assert f"{name}-noble" in checks
        for dependency in dependencies:
            override = check_overrides[dependency]
            assert override["vars"]["charm_build_complete_bin"] is True
            assert set(primary["files"]) <= set(override["files"])


@pytest.mark.parametrize(
    ("base", "requested", "expected"),
    [
        ("ubuntu@24.04", None, ""),
        ("ubuntu@26.04", None, ""),
        ("ubuntu@24.04", "noble-amd64", "amd64"),
        ("ubuntu@26.04", "resolute-amd64", "amd64"),
        ("ubuntu@26.04", "amd64", "amd64"),
        ("ubuntu@24.04", "resolute-amd64", None),
        ("ubuntu@26.04", "noble-amd64", None),
        ("ubuntu@26.04", "resolute-s390x", None),
        (None, "noble-amd64", "noble-amd64"),
        (None, "resolute-amd64", "resolute-amd64"),
        (None, "amd64", None),
        (None, None, None),
    ],
)
def test_build_platform_selection(tmp_path, base, requested, expected):
    recipe = tmp_path / "charmcraft.yaml"
    config = {
        "platforms": {
            "noble-amd64": {"build-for": ["ubuntu@24.04:amd64"]},
            "resolute-amd64": {"build-for": "ubuntu@26.04:amd64"},
        },
    }
    if base:
        config = {"base": base, "platforms": {"amd64": None, "arm64": None}}
    recipe.write_text(yaml.safe_dump(config))

    args = [sys.executable, str(ROOT / "repository.py"), "build-platform", str(recipe)]
    if requested:
        args.append(requested)
    result = subprocess.run(args, capture_output=True, text=True, check=False)

    if expected is None:
        assert result.returncode != 0
        if requested:
            assert f"does not support build platform {requested}" in result.stderr
        else:
            assert "has multiple platforms" in result.stderr
            assert "charm_build_platform" in result.stderr
            assert "noble-amd64, resolute-amd64" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected


@pytest.mark.parametrize("architectures", [("amd64",), ("amd64", "arm64", "s390x")])
def test_single_base_named_platforms_without_selector(tmp_path, architectures):
    recipe = tmp_path / "charmcraft.yaml"
    platforms = {
        f"noble-{arch}": {
            "build-on": [f"ubuntu@24.04:{arch}"],
            "build-for": [f"ubuntu@24.04:{arch}"],
        }
        for arch in architectures
    }
    recipe.write_text(yaml.safe_dump({"platforms": platforms}))
    result = subprocess.run(
        [sys.executable, str(ROOT / "repository.py"), "build-platform", str(recipe)],
        capture_output=True,
        text=True,
        check=False,
    )

    if len(architectures) == 1:
        assert result.returncode == 0, result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode != 0
        assert "has multiple platforms" in result.stderr
        assert ", ".join(platforms) in result.stderr
