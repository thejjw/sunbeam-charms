#!/usr/bin/env python

import logging
import argparse
import dataclasses
import pathlib
import shutil
import subprocess

import yaml

ROOT_DIR = pathlib.Path(__file__).parent
EXTERNAL_LIB_DIR = ROOT_DIR / "libs" / "external" / "lib"
OPS_SUNBEAM_DIR = ROOT_DIR / "ops-sunbeam" / "ops_sunbeam"
BUILD_FILE = ".sunbeam-build.yaml"
UTILITY_FILES = [
    ROOT_DIR / ".stestr.conf",
    ROOT_DIR / ".jujuignore",
]
STORAGE_DIR = "storage"


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


###############################################
# Utility functions
###############################################
@dataclasses.dataclass
class SunbeamBuild:
    path: pathlib.Path
    external_libraries: list[str]
    internal_libraries: list[str]
    templates: list[str]

    @classmethod
    def load(cls, path: pathlib.Path) -> "SunbeamBuild":
        with path.open() as f:
            data = yaml.safe_load(f)
            return cls(
                path=path.parent,
                external_libraries=data.get("external-libraries", []),
                internal_libraries=data.get("internal-libraries", []),
                templates=data.get("templates", []),
            )


def _library_to_path(library: str) -> pathlib.Path:
    split = library.split(".")
    if len(split) != 4:
        raise ValueError(f"Invalid library: {library}")
    return pathlib.Path("/".join(split) + ".py")


def validate_charm(
    charm: str,
    internal_libraries: dict[str, pathlib.Path],
    external_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
) -> SunbeamBuild:
    """Validate the charm."""
    path = ROOT_DIR / "charms" / charm
    if not path.exists():
        raise ValueError(f"Charm {charm} does not exist.")
    build_file = path / BUILD_FILE
    if not build_file.exists():
        raise ValueError(f"Charm {charm} does not have a build file.")
    charm_build = load_charm(charm)

    for library in charm_build.external_libraries:
        if library not in external_libraries:
            raise ValueError(
                f"Charm {charm} has invalid external library: {library} not found."
            )
    for library in charm_build.internal_libraries:
        if library not in internal_libraries:
            raise ValueError(
                f"Charm {charm} has invalid internal library: {library} not found."
            )
    for template in charm_build.templates:
        if template not in templates:
            raise ValueError(
                f"Charm {charm} has invalid template: {template} not found."
            )
    return charm_build


def load_external_libraries() -> dict[str, pathlib.Path]:
    """Load the external libraries."""
    path = EXTERNAL_LIB_DIR
    return {
        str(p.relative_to(path))[:-3].replace("/", "."): p
        for p in path.glob("**/*.py")
    }


def load_internal_libraries() -> dict[str, pathlib.Path]:
    """Load the internal libraries."""
    charms = list((ROOT_DIR / "charms").iterdir())
    libraries = {}
    for charm in charms:
        path = charm / "lib"
        search_path = path / "charms" / charm.name.replace("-", "_")
        libraries.update(
            {
                str(p.relative_to(path))[:-3].replace("/", "."): p
                for p in search_path.glob("**/*.py")
            }
        )
    return libraries


def load_templates() -> dict[str, pathlib.Path]:
    """Load the templates."""
    path = ROOT_DIR / "templates"
    return {str(p.relative_to(path)): p for p in path.glob("**/*")}


def list_charms() -> list[str]:
    """List the available charms."""
    return [
        p.name
        for p in (ROOT_DIR / "charms").iterdir()
        if p.is_dir() and p.name != STORAGE_DIR
    ] + [p.name for p in (ROOT_DIR / "charms" / STORAGE_DIR).iterdir() if p.is_dir()]


def load_charm(charm: str) -> SunbeamBuild:
    """Load the charm build file."""
    path = ROOT_DIR / "charms" / charm / BUILD_FILE
    return SunbeamBuild.load(path)


def copy(src: pathlib.Path, dest: pathlib.Path):
    """Copy the src to dest.

    Only supports files.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)


def prepare_charm(
    charm: SunbeamBuild,
    internal_libraries: dict[str, pathlib.Path],
    external_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    dry_run: bool = False,
):
    """Copy the necessary files.

    Will copy external libraries, ops sunbeam and templates.
    """
    dest = charm.path / "lib" / "ops_sunbeam"
    logger.debug(f"Copying ops sunbeam to {dest}")
    if not dry_run:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(OPS_SUNBEAM_DIR, dest)
    for utility_file in UTILITY_FILES:
        utility_path = utility_file.relative_to(ROOT_DIR)
        dest = charm.path / utility_path
        logger.debug(f"Copying {utility_file} to {dest}")
        if not dry_run:
            copy(utility_file, dest)
    for library in charm.external_libraries:
        path = external_libraries[library]
        library_path = path.relative_to(EXTERNAL_LIB_DIR)
        dest = charm.path / "lib" / library_path
        if not dest.exists():
            logger.debug(f"Copying {library} to {dest}")
            if dry_run:
                continue
            copy(path, dest)
    for library in charm.internal_libraries:
        path = internal_libraries[library]
        library_path = _library_to_path(library)
        dest = charm.path / "lib" / library_path
        if not dest.exists():
            logger.debug(f"Copying {library} to {dest}")
            if dry_run:
                continue
            copy(path, dest)
    for template in charm.templates:
        path = templates[template]
        dest = charm.path / "src" / "templates" / template
        if not dest.exists():
            logger.debug(f"Copying {template} to {dest}")
            if dry_run:
                continue
            copy(path, dest)


def clean_charm(
    charm: SunbeamBuild,
    dry_run: bool = False,
):
    """Clean charm directory.

    Will remove the external libraries, ops sunbeam and templates.
    """
    path = charm.path / "lib" / "ops_sunbeam"
    if path.exists():
        logger.debug(f"Removing {path}")
        if not dry_run:
            shutil.rmtree(path)
    for utility_file in UTILITY_FILES:
        utility_path = utility_file.relative_to(ROOT_DIR)
        path = charm.path / utility_path
        if path.exists():
            logger.debug(f"Removing {path}")
            if not dry_run:
                path.unlink()
    for library in charm.external_libraries + charm.internal_libraries:
        # Remove the charm namespace
        path = charm.path / "lib" / _library_to_path(library).parents[1]
        if path.exists():
            logger.debug(f"Removing {path}")
            if dry_run:
                continue
            shutil.rmtree(path)
    for template in charm.templates:
        path = charm.path / "src" / "templates" / template
        if path.exists():
            logger.debug(f"Removing {path}")
            if dry_run:
                continue
            path.unlink()


###############################################
# Cli Definitions
###############################################
def _add_charm_argument(parser: argparse.ArgumentParser):
    parser.add_argument(
        "charm", type=str, nargs="*", help="The charm to operate on."
    )


def main_cli():
    main_parser = argparse.ArgumentParser(
        description="Sunbeam Repository utilities."
    )
    main_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging."
    )
    subparsers = main_parser.add_subparsers(
        required=True, help="sub-command help"
    )

    prepare_parser = subparsers.add_parser("prepare", help="Prepare charm(s).")
    _add_charm_argument(prepare_parser)
    prepare_parser.add_argument(
        "--clean",
        action="store_true",
        default=False,
        help="Clean the charm(s) first.",
    )
    prepare_parser.add_argument(
        "--dry-run", action="store_true", default=False, help="Dry run."
    )
    prepare_parser.set_defaults(func=prepare_cli)

    clean_parser = subparsers.add_parser("clean", help="Clean charm(s).")
    _add_charm_argument(clean_parser)
    clean_parser.add_argument(
        "--dry-run", action="store_true", default=False, help="Dry run."
    )
    clean_parser.set_defaults(func=clean_cli)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate charm(s)."
    )
    _add_charm_argument(validate_parser)
    validate_parser.set_defaults(func=validate_cli)

    pythonpath_parser = subparsers.add_parser(
        "pythonpath", help="Print the pythonpath."
    )
    pythonpath_parser.set_defaults(func=pythonpath_cli)

    fetch_lib_parser = subparsers.add_parser(
        "fetch-lib", help="Fetch the external libraries."
    )
    fetch_lib_parser.add_argument(
        "libraries", type=str, nargs="*", help="Libraries to fetch."
    )
    fetch_lib_parser.set_defaults(func=fetch_lib_cli)

    args = main_parser.parse_args()
    level = logging.INFO
    if args.verbose:
        level = logging.DEBUG
    logger.setLevel(level)
    context = vars(args)
    context["internal_libraries"] = load_internal_libraries()
    context["external_libraries"] = load_external_libraries()
    context["templates"] = load_templates()
    context["sunbeam_charms"] = list_charms()
    if "charm" in context:
        charms = context.pop("charm")
        if not charms:
            charms = context["sunbeam_charms"]
        context["charms"] = [
            validate_charm(
                charm,
                context["internal_libraries"],
                context["external_libraries"],
                context["templates"],
            )
            for charm in charms
        ]
    args.func(**context)


def prepare_cli(
    charms: list[SunbeamBuild],
    internal_libraries: dict[str, pathlib.Path],
    external_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    clean: bool = False,
    dry_run: bool = False,
    **kwargs,
):
    for charm in charms:
        logger.info("Preparing the charm %s", charm.path.name)
        if clean:
            clean_charm(charm, dry_run=dry_run)
        prepare_charm(
            charm,
            internal_libraries,
            external_libraries,
            templates,
            dry_run=dry_run,
        )


def clean_cli(
    charms: list[SunbeamBuild],
    internal_libraries: dict[str, pathlib.Path],
    external_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    dry_run: bool = False,
    **kwargs,
):
    for charm in charms:
        logger.info("Cleaning the charm %s", charm.path.name)
        clean_charm(charm, dry_run=dry_run)


def validate_cli(
    charms: list[SunbeamBuild],
    internal_libraries: dict[str, pathlib.Path],
    external_libraries: dict[str, pathlib.Path],
    templates: dict[str, pathlib.Path],
    **kwargs,
):
    """No op because done in the main_cli."""
    for charm in charms:
        logging.info("Charm %s is valid.", charm.path.name)


def pythonpath_cli(internal_libraries: dict[str, pathlib.Path], **kwargs):
    """Print the pythonpath."""
    parent_dirs = set()
    for path in internal_libraries.values():
        parent_dirs.add(path.parents[3])
    parent_dirs.add(OPS_SUNBEAM_DIR.parent)
    parent_dirs.add(EXTERNAL_LIB_DIR)
    print(":".join(str(p) for p in parent_dirs))


def fetch_lib_cli(
    libraries: list[str], external_libraries: dict[str, pathlib.Path], **kwargs
):
    """Fetch the external libraries."""
    cwd = EXTERNAL_LIB_DIR.parent
    libraries_set = set(libraries)
    unknown_libraries = libraries_set - set(external_libraries.keys())
    if unknown_libraries:
        raise ValueError(f"Unknown libraries: {unknown_libraries}")
    if not libraries_set:
        libraries_set = set(external_libraries.keys())
    for library in libraries_set:
        logging.info(f"Fetching {library}")
        # Fetch the library
        subprocess.run(
            ["charmcraft", "fetch-lib", library], cwd=cwd, check=True
        )


if __name__ == "__main__":
    main_cli()
