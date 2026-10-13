# Copyright 2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Validate operator supplied configuration overrides.

This script is pushed into the workload container by the charm and executed
with the interpreter that runs the workload (see
:mod:`ops_sunbeam.config_overrides`).  It performs the checks described in the
config file override design:

* ``syntax``: the candidate file parses as an INI (oslo style) file.  Duplicate
  options are reported as warnings because oslo.config resolves them with
  last-one-wins semantics.
* ``managed_keys``: options that already appear in the config file rendered by
  the charm are considered owned by the charm (either juju managed or an
  opinionated default) and cannot be overridden unless explicitly listed as
  overridable.
* ``blocklist``: options that must never be overridden (wiring that would break
  the deployment), maintained per charm.
* ``schema``: option names are checked against the options registered by the
  service itself (``oslo.config.opts`` entry points) and values are typed
  checked by oslo.config.  This is what makes OpenStack itself the source of
  truth: no per-option list has to be maintained by the charm.
* ``policy``: optional policy file checked against the rules registered by the
  service (``oslo.policy.policies`` entry points).
"""

from __future__ import (
    annotations,
)

import argparse
import configparser
import importlib.metadata
import json
import logging
import sys
from typing import (
    Any,
)

logger = logging.getLogger("config-override-validator")

DEFAULT_SENTINEL = "@@default@@"

# Namespaces always attempted when checking option names.  Missing namespaces
# are reported as warnings, not errors, so that this list can grow with the
# services without breaking smaller workloads.
COMMON_NAMESPACES = (
    "oslo.log",
    "oslo.messaging",
    "oslo.middleware",
    "oslo.policy",
    "oslo.db",
    "oslo.concurrency",
    "oslo.service.service",
    "oslo.service.periodic_task",
    "oslo.privsep",
    "keystonemiddleware.auth_token",
)

# Groups whose sample/registered data is known to be incomplete.  Mirrors the
# allowlist used by oslo-config-validator.
INCOMPLETE_GROUPS = ("keystone_authtoken",)

CHECK_OK = "ok"
CHECK_FAILED = "failed"
CHECK_SKIPPED = "skipped"


def normalize(name: str) -> str:
    """Normalise an option or group name for comparisons."""
    return name.strip().lower().replace("-", "_")


def normalize_group(name: str) -> str:
    """Normalise a config group name."""
    normalized = normalize(name)
    if normalized in ("@@default@@", "default"):
        return "DEFAULT"
    return normalized


def parse_ini(
    text: str,
) -> tuple[dict[str, dict[str, str]], list[str], list[str]]:
    """Parse an INI document.

    :returns: tuple of (sections, errors, warnings).  ``sections`` maps the
        normalised section name to a dict of normalised option name -> value.
    """
    errors: list[str] = []
    warnings: list[str] = []
    parser = configparser.RawConfigParser(strict=False, allow_no_value=True)
    # Make [DEFAULT] an ordinary section so that its options are not merged
    # into every other section by configparser.
    parser.default_section = DEFAULT_SENTINEL
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        errors.append(f"syntax error: {exc}")
        return {}, errors, warnings

    sections: dict[str, dict[str, str]] = {}
    for raw_section in parser.sections():
        group = normalize_group(raw_section)
        options: dict[str, str] = {}
        for raw_key, value in parser.items(raw_section):
            options[normalize(raw_key)] = value or ""
        sections[group] = options

    warnings.extend(find_duplicate_options(text))
    return sections, errors, warnings


def find_duplicate_options(text: str) -> list[str]:
    """Report options defined more than once in a candidate file.

    ``oslo.config`` resolves duplicates with last-one-wins semantics, so this
    is a warning rather than an error.
    """
    warnings: list[str] = []
    seen: dict[str, set[str]] = {}
    group = "DEFAULT"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            group = normalize_group(stripped[1:-1])
            continue
        if "=" not in stripped:
            continue
        key = normalize(stripped.split("=", 1)[0])
        group_seen = seen.setdefault(group, set())
        if key in group_seen:
            warnings.append(
                f"duplicate option {group}/{key} (last value wins)"
            )
        group_seen.add(key)
    return warnings


def _match_rule(rule: str, group: str, key: str) -> bool:
    """Match a ``section/key`` style rule against a candidate option."""
    rule = normalize(rule)
    group = normalize(group)
    key = normalize(key)
    if "/" in rule:
        rule_group, rule_key = rule.split("/", 1)
    else:
        rule_group, rule_key = "*", rule
    if rule_group not in ("*", group):
        return False
    return rule_key in ("*", key)


def matching_rules(rules: list[str], group: str, key: str) -> list[str]:
    """Return all rules matching the given option."""
    return [rule for rule in rules if _match_rule(rule, group, key)]


def check_option_sets(
    candidate: dict[str, dict[str, str]],
    blocklist: list[str],
    overridable: list[str],
    managed: dict[str, set[str]],
) -> tuple[list[str], list[str], list[str]]:
    """Check blocklist and charm managed options.

    :returns: tuple of (errors, blocklist_hits, managed_conflicts).
    """
    errors: list[str] = []
    blocklist_hits: list[str] = []
    managed_conflicts: list[str] = []
    for group, options in candidate.items():
        for key in options:
            if matching_rules(blocklist, group, key):
                blocklist_hits.append(f"{group}/{key}")
                continue
            if group in managed and key in managed[group]:
                if matching_rules(overridable, group, key):
                    continue
                managed_conflicts.append(f"{group}/{key}")
    for hit in blocklist_hits:
        errors.append(f"{hit} is blocked and cannot be overridden")
    for conflict in managed_conflicts:
        errors.append(
            f"{conflict} is managed by the charm and cannot be overridden"
        )
    return errors, blocklist_hits, managed_conflicts


def _select_namespaces(
    explicit: list[str], prefixes: list[str]
) -> tuple[list[str], list[str]]:
    """Resolve the oslo.config.opts namespaces to load."""
    available = sorted(
        entry.name
        for entry in importlib.metadata.entry_points(group="oslo.config.opts")
    )
    selected: list[str] = []
    for name in explicit:
        if name not in selected:
            selected.append(name)
    for name in COMMON_NAMESPACES:
        if name in available and name not in selected:
            selected.append(name)
    for prefix in prefixes:
        for name in available:
            if name.startswith(prefix) and name not in selected:
                selected.append(name)
    unknown_explicit = [name for name in explicit if name not in available]
    return selected, unknown_explicit


def load_registered_options(
    namespaces: list[str],
) -> tuple[
    dict[str, dict[str, Any]],
    list[str],
]:
    """Load option data registered by the service.

    :returns: tuple of (options_by_group, errors).  ``options_by_group`` maps
        normalised group name to ``{normalised_name: opt}``.
    """
    errors: list[str] = []
    options: dict[str, dict[str, Any]] = {}
    try:
        from stevedore import (
            named,
        )
    except ImportError:
        errors.append("stevedore is not available in the workload container")
        return options, errors

    def _on_load_failure(
        manager: Any, entrypoint: Any, exception: BaseException
    ) -> None:
        errors.append(
            f"namespace {entrypoint.name} failed to load: {exception}"
        )

    try:
        manager: Any = named.NamedExtensionManager(
            "oslo.config.opts",
            names=namespaces,
            on_load_failure_callback=_on_load_failure,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"failed to load option namespaces: {exc}")
        return options, errors

    return _collect_options(manager, errors), errors


def _collect_options(
    manager: Any, errors: list[str]
) -> dict[str, dict[str, Any]]:
    """Collect the options registered by each loaded namespace.

    ``extension.plugin`` is the ``list_opts`` style callable the service
    publishes for the namespace: oslo loads these entry points without
    invoking them (``invoke_on_load=False``) and calls the plugin itself.
    """
    options: dict[str, dict[str, Any]] = {}
    for extension in manager:
        try:
            opt_groups = extension.plugin()
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"namespace {extension.name} failed to list options: {exc}"
            )
            continue
        for group, opts in opt_groups:
            group_name = normalize_group(
                getattr(group, "name", group) or DEFAULT_SENTINEL
            )
            for opt in opts:
                options.setdefault(group_name, {})[normalize(opt.name)] = opt
    return options


def _unknown_options(
    candidate: dict[str, dict[str, str]],
    options: dict[str, dict[str, Any]],
    excluded: set[str],
    incomplete: set[str],
) -> list[str]:
    """Report candidate options the service does not define."""
    unknown: list[str] = []
    for group, group_options in candidate.items():
        if group in excluded or group in incomplete:
            continue
        registered = options.get(group)
        for key in group_options:
            if registered is None or key not in registered:
                unknown.append(f"{group}/{key}")
    return unknown


def _register_options(
    cfg: Any, conf: Any, options: dict[str, dict[str, Any]]
) -> None:
    """Register the options the service defines on a config parser."""
    for group, group_options in options.items():
        opts = list(group_options.values())
        try:
            if group == DEFAULT_SENTINEL or group == "DEFAULT":
                conf.register_opts(opts)
            else:
                conf.register_opts(opts, group)
        except cfg.DuplicateOptError:
            logger.debug(
                "ignoring duplicate option registration for %s", group
            )


def _check_values(
    cfg: Any,
    conf: Any,
    candidate: dict[str, dict[str, str]],
    options: dict[str, dict[str, Any]],
    excluded: set[str],
) -> tuple[list[str], list[str]]:
    """Type check candidate values through oslo.config.

    :returns: tuple of (invalid_values, deprecated_options).
    """
    invalid: list[str] = []
    deprecated: list[str] = []
    for group, group_options in candidate.items():
        if group in excluded:
            continue
        registered = options.get(group, {})
        for key, value in group_options.items():
            opt = registered.get(key)
            if opt is None:
                continue
            dest = getattr(opt, "dest", key)
            try:
                if group == "DEFAULT":
                    getattr(conf, dest)
                else:
                    conf[group][dest]
            except cfg.NoSuchOptError:
                continue
            except Exception as exc:  # noqa: BLE001
                invalid.append(
                    f"{group}/{key} = {value!r} is not a valid value: {exc}"
                )
            else:
                if _is_deprecated(opt):
                    deprecated.append(f"{group}/{key}")
    return invalid, deprecated


def check_schema(
    candidate: dict[str, dict[str, str]],
    candidate_path: str,
    namespaces: list[str],
    exclude_groups: list[str],
    unknown_is_warning: bool,
) -> dict[str, Any]:
    """Check option names and values against the service's own options."""
    result: dict[str, Any] = {
        "status": CHECK_SKIPPED,
        "namespaces": namespaces,
        "namespace_errors": [],
        "unknown_options": [],
        "deprecated_options": [],
        "invalid_values": [],
    }
    options, errors = load_registered_options(namespaces)
    result["namespace_errors"] = errors
    if not options:
        result["status"] = CHECK_SKIPPED
        return result

    try:
        from oslo_config import (
            cfg,
        )
    except ImportError as exc:
        result["namespace_errors"].append(
            f"oslo.config is not available: {exc}"
        )
        result["status"] = CHECK_SKIPPED
        return result

    excluded = {normalize_group(g) for g in exclude_groups}
    incomplete = {normalize_group(g) for g in INCOMPLETE_GROUPS}
    result["unknown_options"] = _unknown_options(
        candidate, options, excluded, incomplete
    )

    # Type check values by handing the candidate to oslo.config, registered
    # with the options the service defines.
    conf = cfg.ConfigOpts()
    _register_options(cfg, conf, options)
    try:
        conf(["--config-file", candidate_path])
    except Exception as exc:  # noqa: BLE001
        # A failure here means the file cannot be parsed by oslo.config at all.
        result["namespace_errors"].append(
            f"oslo.config failed to parse file: {exc}"
        )
        result["status"] = CHECK_FAILED
        return result

    invalid, deprecated = _check_values(
        cfg, conf, candidate, options, excluded
    )
    result["invalid_values"] = invalid
    result["deprecated_options"] = deprecated
    result["multi_options"] = [
        f"{group}/{key}"
        for group, group_options in options.items()
        for key, opt in group_options.items()
        if isinstance(opt, cfg.MultiStrOpt) or getattr(opt, "multi", False)
    ]
    unknown = result["unknown_options"]
    failed = bool(unknown and not unknown_is_warning) or bool(invalid)
    result["status"] = CHECK_FAILED if failed else CHECK_OK
    return result


def _is_deprecated(opt: Any) -> bool:
    """Whether an oslo.config option is deprecated."""
    if getattr(opt, "deprecated_for_removal", False):
        return True
    if getattr(opt, "deprecated_since", None):
        return True
    deprecated_opts = getattr(opt, "deprecated_opts", None) or []
    for deprecated in deprecated_opts:
        # The option itself being an alias of a currently named option means
        # the name used in the file is deprecated.
        if normalize(getattr(deprecated, "name", "")) == normalize(opt.name):
            return True
    return False


def _load_policy_file(
    policy_path: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Read a policy file into a rule mapping.

    Syntax errors and unavailable dependencies are reported through
    ``result``; ``None`` means the rules could not be read.
    """
    try:
        import yaml
    except ImportError as exc:
        result["warnings"].append(f"PyYAML is not available: {exc}")
        return None

    with open(policy_path) as fh:
        text = fh.read()
    try:
        rules = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        result["syntax_errors"].append(f"policy file is not valid YAML: {exc}")
        return None
    if not isinstance(rules, dict):
        result["syntax_errors"].append(
            "policy file must be a mapping of rule name to rule definition"
        )
        return None
    for name, rule in rules.items():
        if not isinstance(rule, (str, list)):
            result["syntax_errors"].append(
                f"policy rule {name!r} must be a string or a list of strings"
            )
    return rules


def _known_policy_rules(
    policy_namespace: str | None, result: dict[str, Any]
) -> set[str]:
    """Rules published by the service's oslo.policy.policies namespace."""
    known_rules: set[str] = set()
    if not policy_namespace:
        return known_rules
    try:
        from stevedore import (
            named,
        )
    except ImportError as exc:
        result["warnings"].append(f"stevedore is not available: {exc}")
        return known_rules
    try:
        manager: Any = named.NamedExtensionManager(
            "oslo.policy.policies",
            names=[policy_namespace],
        )
        for extension in manager:
            for rule in extension.plugin() or []:
                known_rules.add(rule.name)
    except Exception as exc:  # noqa: BLE001
        result["warnings"].append(
            f"policy namespace {policy_namespace} unavailable: {exc}"
        )
    return known_rules


def _parse_policy_rules(rules: dict[str, Any], result: dict[str, Any]) -> None:
    """Ask oslo.policy itself to parse the rules when available."""
    try:
        # oslo.policy lives in the workload container, not in the charm's
        # test environment, so mypy cannot resolve the module here.
        from oslo_policy import policy as oslo_policy  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        parsed = oslo_policy.Rules.from_dict(rules)
    except Exception as exc:  # noqa: BLE001
        result["syntax_errors"].append(f"invalid policy rule syntax: {exc}")
    else:
        result["rules"] = len(parsed)


def _referenced_policy_rules(rules: dict[str, Any]) -> set[str]:
    """Rule names referenced as ``rule:<name>`` inside the policy file."""
    referenced: set[str] = set()
    for value in rules.values():
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, str):
                continue
            for token in item.replace("(", " ").replace(")", " ").split():
                token = token.lstrip("!")
                if token.startswith("rule:") and len(token) > 5:
                    referenced.add(token[5:])
    return referenced


def check_policy(
    policy_path: str | None,
    policy_namespace: str | None,
) -> dict[str, Any]:
    """Validate a candidate policy file."""
    result: dict[str, Any] = {
        "status": CHECK_SKIPPED,
        "unknown_rules": [],
        "syntax_errors": [],
        "warnings": [],
    }
    if not policy_path:
        return result
    rules = _load_policy_file(policy_path, result)
    if rules is None or result["syntax_errors"]:
        result["status"] = (
            CHECK_FAILED if result["syntax_errors"] else CHECK_SKIPPED
        )
        return result

    known_rules = _known_policy_rules(policy_namespace, result)
    referenced = _referenced_policy_rules(rules)
    unknown_rules = [
        name
        for name in rules
        if known_rules and name not in known_rules and name not in referenced
    ]
    result["unknown_rules"] = unknown_rules
    if unknown_rules:
        result["syntax_errors"].append(
            "unknown policy rules: " + ", ".join(sorted(unknown_rules))
        )
    _parse_policy_rules(rules, result)

    result["status"] = CHECK_FAILED if result["syntax_errors"] else CHECK_OK
    return result


def managed_options(main_conf_path: str | None) -> dict[str, set[str]]:
    """Return the options the charm itself renders."""
    if not main_conf_path:
        return {}
    try:
        with open(main_conf_path) as fh:
            text = fh.read()
    except OSError as exc:
        logger.warning("cannot read %s: %s", main_conf_path, exc)
        return {}
    sections, errors, _warnings = parse_ini(text)
    if errors:
        logger.warning(
            "cannot parse charm rendered config %s: %s", main_conf_path, errors
        )
        return {}
    return {group: set(options) for group, options in sections.items()}


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="candidate INI config file")
    parser.add_argument(
        "--main-conf",
        help="config file rendered by the charm, used to detect managed options",
    )
    parser.add_argument("--policy", help="candidate policy YAML file")
    parser.add_argument(
        "--policy-namespace",
        help="oslo.policy.policies namespace for the service",
    )
    parser.add_argument(
        "--blocklist",
        default="[]",
        help="JSON list of options that cannot be overridden",
    )
    parser.add_argument(
        "--overridable",
        default="[]",
        help="JSON list of managed options that may be overridden",
    )
    parser.add_argument(
        "--namespace",
        action="append",
        default=[],
        help="exact oslo.config.opts namespace to load (repeatable)",
    )
    parser.add_argument(
        "--namespace-prefix",
        action="append",
        default=[],
        help="include oslo.config.opts namespaces with this prefix",
    )
    parser.add_argument(
        "--exclude-group",
        action="append",
        default=[],
        help="skip schema checks for this group",
    )
    parser.add_argument(
        "--unknown-is-warning",
        action="store_true",
        help="report unknown options as warnings instead of errors",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the validator, print JSON and return the exit code."""
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    args = build_parser().parse_args(argv)

    checks: dict[str, str] = {}
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {}

    if not args.config and not args.policy:
        print(json.dumps({"valid": False, "errors": ["nothing to validate"]}))
        return 1

    blocklist = json.loads(args.blocklist)
    overridable = json.loads(args.overridable)

    if args.config:
        with open(args.config) as fh:
            candidate_text = fh.read()
        candidate, syntax_errors, syntax_warnings = parse_ini(candidate_text)
        errors.extend(syntax_errors)
        checks["syntax"] = CHECK_FAILED if syntax_errors else CHECK_OK

        managed = managed_options(args.main_conf)
        set_errors, blocklist_hits, managed_conflicts = check_option_sets(
            candidate, blocklist, overridable, managed
        )
        errors.extend(set_errors)
        checks["blocklist"] = CHECK_FAILED if blocklist_hits else CHECK_OK
        checks["managed_keys"] = (
            CHECK_FAILED if managed_conflicts else CHECK_OK
        )
        result["blocklist_hits"] = blocklist_hits
        result["managed_conflicts"] = managed_conflicts

        namespaces, unknown_explicit = _select_namespaces(
            args.namespace, args.namespace_prefix
        )
        for name in unknown_explicit:
            warnings.append(
                f"namespace {name} not available in workload container"
            )
        schema = check_schema(
            candidate,
            args.config,
            namespaces,
            args.exclude_group,
            args.unknown_is_warning,
        )
        checks["schema"] = schema["status"]
        result["schema"] = schema
        multi_opts = set(schema.get("multi_options", ()))
        warnings.extend(
            w
            for w in syntax_warnings
            if not any(
                w.startswith(f"duplicate option {opt} ") for opt in multi_opts
            )
        )
        for unknown in schema["unknown_options"]:
            message = f"{unknown} is not a known option for this service"
            if args.unknown_is_warning:
                warnings.append(message)
            else:
                errors.append(message)
        errors.extend(schema["invalid_values"])
        warnings.extend(
            f"{opt} is deprecated" for opt in schema["deprecated_options"]
        )
        warnings.extend(schema["namespace_errors"])

    if args.policy:
        policy = check_policy(args.policy, args.policy_namespace)
        checks["policy"] = policy["status"]
        result["policy"] = policy
        errors.extend(policy["syntax_errors"])
        warnings.extend(policy["warnings"])

    payload = {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        **result,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
