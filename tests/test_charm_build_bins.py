import pathlib

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
        assert "rebuild" in job["files"]

        for build in job["vars"]["charm_builds"]:
            charm_path = pathlib.Path(build["path"])
            assert build["name"] == charm_path.name
            assert f"charms/{build['path']}/" in job["files"]
            assert (ROOT / "charms" / build["path"] / "charmcraft.yaml").exists()


def test_build_template_uses_only_concrete_bin_jobs():
    jobs = _jobs()
    template = _project_template("openstack-sunbeam-charm-build-jobs")
    expected_jobs = list(_bin_jobs(jobs))

    assert _pipeline_jobs(template, "check") == expected_jobs
    assert _pipeline_jobs(template, "gate") == expected_jobs


def test_charm_build_jobs_are_only_bins():
    charm_build_jobs = [
        name
        for name in _jobs()
        if name.startswith("charm-build-") and name != "charm-build-bin"
    ]

    assert charm_build_jobs == list(_bin_jobs(_jobs()))


def test_functional_jobs_depend_on_bins_for_artifact_downloads():
    jobs = _jobs()
    bin_jobs = set(_bin_jobs(jobs))
    functional_jobs = [
        job
        for job in jobs.values()
        if job["name"].startswith("func-test-") and "charm_jobs" in job.get("vars", {})
    ]

    assert functional_jobs
    for job in functional_jobs:
        dependencies = {dependency["name"] for dependency in job["dependencies"]}
        charm_jobs = set(job["vars"]["charm_jobs"])
        assert dependencies <= bin_jobs
        assert charm_jobs == dependencies
        assert all(dependency["soft"] is True for dependency in job["dependencies"])
