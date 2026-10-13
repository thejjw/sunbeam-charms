# Copyright 2026 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

r"""Service configuration file overrides for sunbeam charms.

The overrides are supplied by the user as juju file resources attached
with ``juju attach-resource``: ``config-override`` (filename
``override.conf``) carries the service configuration file override and
``policy-override`` (filename ``override.yaml``) the policy override.
A missing resource and an empty file both mean "no override".

Charms apply the content by writing it to a drop-in file that the service
reads *after* the file the charm renders itself, so charm ownership of the
base configuration is preserved.  Before the content is written it is
validated inside the workload container by
:mod:`ops_sunbeam.config_override_validator`, which uses the service's own
oslo.config and oslo.policy metadata.  Validation happens both in the
``config-changed`` flow (content that does not validate is never applied,
and the previous overrides stay in place) and on demand through the
``validate-config-overrides`` and ``apply-config-overrides`` actions.
"""

import hashlib
import json
import logging
import os
import time
from dataclasses import (
    dataclass,
    field,
)
from pathlib import (
    Path,
)
from typing import (
    TYPE_CHECKING,
    Any,
)

import ops
import ops_sunbeam.core as sunbeam_core

if TYPE_CHECKING:
    from ops_sunbeam.charm import (
        OSBaseOperatorCharm,
    )

logger = logging.getLogger(__name__)

#: How long an apply waits for juju to finish writing the attached override
#: resources.  ``juju attach-resource`` replaces the unit and truncates the
#: resource file while the agent copies the new bytes in, so an action that
#: runs in that window reads attached resources as empty.  Empty attached
#: resources are how a clear is requested, so the apply waits for the write
#: before it decides.
RESOURCE_SETTLE_TIMEOUT = 30.0

#: How often the resources are re-read while waiting for them to settle.
RESOURCE_SETTLE_INTERVAL = 2.0

VALIDATOR_SCRIPT = "config_override_validator.py"
VALIDATOR_REMOTE_DIR = "/tmp/.sunbeam-config-override"
VALIDATOR_REMOTE_PATH = f"{VALIDATOR_REMOTE_DIR}/{VALIDATOR_SCRIPT}"
CANDIDATE_CONF_PATH = f"{VALIDATOR_REMOTE_DIR}/candidate.conf"
CANDIDATE_POLICY_PATH = f"{VALIDATOR_REMOTE_DIR}/candidate.yaml"
BASELINE_CONF_PATH = f"{VALIDATOR_REMOTE_DIR}/baseline.conf"

DROPIN_HEADER = (
    "# This file is managed by the sunbeam config-file override commands.\n"
    "# Do not edit: changes to this file are overwritten.\n"
)
DROPIN_SUFFIX = ".d/zz-config-overrides.conf"
POLICY_DIRNAME = "policy.d"
POLICY_DROPIN_NAME = "zz-config-overrides.yaml"
DEFAULT_BLOCKLIST = ("database/connection",)


@dataclass(frozen=True)
class ConfigOverrideSpec:
    """Service specific details about where and how overrides apply."""

    #: Configuration file rendered by the charm that the service reads
    #: first.  Also used to detect the options the charm manages itself.
    main_conf: str
    #: Name of the container holding the service.  When unset the container
    #: is the one that renders ``main_conf``.
    container: str | None = None
    #: File the override content is written to.  The service must read it
    #: after ``main_conf``.
    dropin_path: str | None = None
    #: File policy override content is written to.  When unset policy
    #: overrides are rejected.
    policy_dropin_path: str | None = None
    #: ``oslo.policy.policies`` namespace of the service, used to check rule
    #: names.
    policy_namespace: str | None = None
    #: ``oslo.config.opts`` namespaces of the service, used to check option
    #: names and values.
    namespace: tuple[str, ...] = ()
    #: Automatically include every ``oslo.config.opts`` namespace starting
    #: with one of these prefixes.
    namespace_prefix: tuple[str, ...] = ()
    #: Config groups excluded from option checks.
    exclude_group: tuple[str, ...] = ()
    #: Options the user may never override (``section/key``, ``key`` or
    #: ``section/*``).
    blocklist: tuple[str, ...] = ()
    #: Exceptions to the charm managed option check.
    overridable: tuple[str, ...] = ()
    #: Ownership of the drop-in files.
    file_user: str | None = None
    file_group: str | None = None
    file_permissions: int = 0o644
    #: Report unknown options as warnings instead of errors.  Useful for
    #: services whose option list the validator cannot load completely.
    unknown_is_warning: bool = False
    #: Name of the juju file resource carrying the configuration override.
    resource: str = "config-override"
    #: Name of the juju file resource carrying the policy override.
    policy_resource: str = "policy-override"
    #: Environment variable, if any, the service reads to discover its
    #: configuration files.  When set, the charm sets it to the full ordered
    #: list of files for the service process.
    service_env: str | None = None
    #: Timeout in seconds for the validation run in the container.
    validation_timeout: int = 180

    def __post_init__(self) -> None:
        """Validate the spec."""
        if not self.dropin_path and not self.policy_dropin_path:
            raise ValueError(
                "ConfigOverrideSpec requires dropin_path or "
                "policy_dropin_path"
            )

    @property
    def policy_supported(self) -> bool:
        """Whether policy overrides can be applied."""
        return self.policy_dropin_path is not None

    @property
    def dropin_dir(self) -> str | None:
        """Directory the service reads the override drop-in from."""
        if not self.dropin_path:
            return None
        return os.path.dirname(self.dropin_path)

    def container_config_file(self) -> sunbeam_core.ContainerConfigFile:
        """Drop-in configuration file to write overrides to."""
        if not self.dropin_path:
            raise ValueError("the spec does not define a drop-in path")
        return sunbeam_core.ContainerConfigFile(
            self.dropin_path,
            self.file_user,
            self.file_group,
            self.file_permissions,
        )

    def policy_container_config_file(self) -> sunbeam_core.ContainerConfigFile:
        """Drop-in policy file to write overrides to."""
        if not self.policy_dropin_path:
            raise ValueError("the spec does not define a policy drop-in path")
        return sunbeam_core.ContainerConfigFile(
            self.policy_dropin_path,
            self.file_user,
            self.file_group,
            self.file_permissions,
        )


def spec_from_convention(
    main_conf: str,
    namespace: str,
    *,
    file_user: str | None = None,
    file_group: str | None = None,
    policy: bool = False,
    service_env: str | None = None,
) -> ConfigOverrideSpec:
    """Spec implied by the charm naming conventions.

    The override content is written to ``<main_conf>.d``, the directory
    oslo looks for by default: ``oslo_config.cfg.find_config_dirs`` returns
    the existing ``<project>.conf.d`` and ``<prog>.conf.d`` directories
    under ``/etc/<project>`` and ``/etc`` (and the user's home), and oslo
    reads the ``*.conf`` files in them after the configuration files the
    service was given (``--config-file``) or found by default, so the
    overrides take precedence over the configuration the charm renders.
    Charms whose service resolves its configuration differently either pass
    ``<main_conf>.d`` as ``--config-dir`` or set ``service_env``: the charm
    then sets that variable to the ordered list of files the service must
    read, including the drop-in while an override is applied.

    :param main_conf: absolute path of the configuration file the charm
        renders
    :param namespace: ``oslo.config.opts``/``oslo.policy.policies`` prefix
    :param policy: whether the service reads the drop-in policy file, i.e.
        renders ``[oslo_policy] policy_dirs`` pointing at ``policy.d``.  When
        unset policy overrides are rejected: they would never be read.
    :param service_env: environment variable the service reads to discover
        its configuration files, when it does not take them on the command
        line.
    """
    directory = os.path.dirname(main_conf)
    return ConfigOverrideSpec(
        main_conf=main_conf,
        dropin_path=f"{main_conf}{DROPIN_SUFFIX}",
        policy_dropin_path=(
            f"{directory}/{POLICY_DIRNAME}/{POLICY_DROPIN_NAME}"
            if policy
            else None
        ),
        policy_namespace=namespace if policy else None,
        namespace_prefix=(namespace,),
        blocklist=DEFAULT_BLOCKLIST,
        file_user=file_user,
        file_group=file_group,
        service_env=service_env,
    )


@dataclass
class ValidationResult:
    """Outcome of validating override file content."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    raw: str = ""

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], raw: str = ""
    ) -> "ValidationResult":
        """Build a result from the validator script output."""
        return cls(
            valid=bool(data.get("valid")),
            errors=[str(e) for e in data.get("errors", [])],
            warnings=[str(w) for w in data.get("warnings", [])],
            checks={
                str(k): str(v) for k, v in (data.get("checks") or {}).items()
            },
            details={
                k: v
                for k, v in data.items()
                if k not in ("valid", "errors", "warnings", "checks")
            },
            raw=raw,
        )

    @classmethod
    def from_validator_output(
        cls, output: str, stderr: str = ""
    ) -> "ValidationResult":
        """Build a result from the validator script stdout.

        :raises ValueError: if the output is not the validator JSON.
        """
        try:
            data = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"validator did not report a result: {exc}: "
                f"{stderr.strip() or output.strip()}"
            ) from exc
        if not isinstance(data, dict) or "valid" not in data:
            raise ValueError(
                f"validator did not report a result: {output.strip()}"
            )
        return cls.from_dict(data, raw=output)

    @classmethod
    def failure(
        cls, message: str, check: str = "content"
    ) -> "ValidationResult":
        """Build a failed result without running the validator."""
        return cls(valid=False, errors=[message], checks={check: "failed"})

    def summary(self) -> str:
        """One line human readable summary."""
        if self.valid:
            if not self.checks:
                return "configuration overrides are valid"
            return "configuration overrides are valid: " + ", ".join(
                f"{name}={status}"
                for name, status in sorted(self.checks.items())
            )
        if self.errors:
            return self.errors[0]
        return "configuration overrides are not valid"

    def to_action_results(self) -> dict[str, str]:
        """Render the result as action results."""
        return {
            "valid": "true" if self.valid else "false",
            "summary": self.summary(),
            "errors": json.dumps(self.errors),
            "warnings": json.dumps(self.warnings),
            "checks": json.dumps(self.checks, sort_keys=True),
            "details": json.dumps(self.details, sort_keys=True),
            "log": self.raw,
        }


class ConfigOverrideManager(ops.Object):
    """Validate and apply the service configuration overrides."""

    _stored = ops.StoredState()

    def __init__(
        self,
        charm: "OSBaseOperatorCharm",
        spec: ConfigOverrideSpec,
    ) -> None:
        """Run constructor."""
        super().__init__(charm, key=f"config-override-{spec.resource}")
        self.charm = charm
        self.spec = spec
        self._stored.set_default(applied_digest=None)
        self._validation_cache: (
            tuple[tuple[str | None, str | None], ValidationResult] | None
        ) = None
        self._validator_pushed: set[str] = set()
        self._removal_requested = False

    def request_removal(self) -> None:
        """Allow an empty payload to clear the applied overrides.

        Only an explicit apply clears the overrides: ``juju
        attach-resource`` restarts the unit, juju replaces the resource
        file while the hooks run and a hook can therefore read an attached
        resource as empty.  A hook reading empty attached resources is not
        the user clearing the overrides, so the drop-ins of the payload
        that is still configured have to stay in place.  An apply that reads
        the resources as empty waits for juju to settle the attached files
        first (:meth:`wait_for_settled_resources`).
        """
        self._removal_requested = True

    # Resource content handling

    def resource_text(self, name: str) -> str | None:
        """Raw text of an attached override resource.

        A resource the charm does not declare, a missing resource and an
        empty file all mean "no override" and are never an error.
        """
        if name not in (self.charm.meta.resources or {}):
            logger.debug("resource %s is not declared by the charm", name)
            return None
        try:
            path = self.charm.model.resources.fetch(name)
        except (ops.ModelError, RuntimeError):
            logger.debug("resource %s is not attached", name)
            return None
        text = path.read_text(encoding="utf-8")
        return text if text.strip() else None

    def resource_attached(self, name: str) -> bool:
        """Whether an override resource is attached and readable.

        Unlike :meth:`resource_text`, an attached *empty* resource counts as
        attached: an empty file is how a user clears an override.  A resource
        juju cannot read right now (it replaces the file while the unit
        restarts) says nothing about what the user wants applied.
        """
        if name not in (self.charm.meta.resources or {}):
            return False
        try:
            self.charm.model.resources.fetch(name)
        except (ops.ModelError, RuntimeError):
            return False
        return True

    def _resource_names(self) -> list[str]:
        """Override resources the charm declares."""
        return [
            name
            for name in (self.spec.resource, self.spec.policy_resource)
            if name in (self.charm.meta.resources or {})
        ]

    def resources_attached(self) -> bool:
        """Whether every declared override resource is attached and readable.

        All of them have to be readable, not just one: ``juju
        attach-resource`` recreates the unit and the newly attached file is
        briefly unavailable while the other half is already attached and
        reads as empty.  Reading that as "the user cleared the overrides"
        would delete the drop-ins of a payload that is still configured, so
        an unreadable resource keeps whatever was applied in place.
        """
        names = self._resource_names()
        return bool(names) and all(
            self.resource_attached(name) for name in names
        )

    def config_text(self) -> str | None:
        """Content of the configuration override resource."""
        return self.resource_text(self.spec.resource)

    def policy_text(self) -> str | None:
        """Content of the policy override resource."""
        return self.resource_text(self.spec.policy_resource)

    def attached(self) -> bool:
        """Whether any override content is attached."""
        return self.config_text() is not None or self.policy_text() is not None

    def digest(self) -> str:
        """Stable digest of the override content."""
        digest = hashlib.sha256()
        digest.update((self.config_text() or "").encode())
        digest.update(b"\0")
        digest.update((self.policy_text() or "").encode())
        return digest.hexdigest()

    def config_content(self) -> str | None:
        """Content of the drop-in configuration file."""
        text = self.config_text()
        return DROPIN_HEADER + text if text is not None else None

    def policy_content(self) -> str | None:
        """Content of the drop-in policy file."""
        text = self.policy_text()
        if text is None or not self.spec.policy_supported:
            return None
        return DROPIN_HEADER + text

    def wait_for_settled_resources(self) -> None:
        """Wait for juju to finish writing the attached override resources.

        ``juju attach-resource`` replaces the unit and truncates the
        resource file while the agent copies the new bytes in, so an action
        that runs in that window reads attached resources as empty.  Empty
        attached resources are how a clear is requested, so an apply waits
        for the write to finish before it decides: content that shows up is
        validated as usual, and resources that stay empty while they are
        attached are the user clearing the overrides.

        The wait only covers the ambiguous case, attached resources that
        read as empty.  Nothing attached, and content already readable,
        return at once.
        """
        if self.attached() or not self.resources_attached():
            return
        deadline = time.monotonic() + RESOURCE_SETTLE_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(RESOURCE_SETTLE_INTERVAL)
            if self.attached():
                logger.debug("override resources settled while waiting")
                return
        logger.debug(
            "override resources are attached and stay empty while waiting"
        )

    # Applying the overrides

    def ensure_directories(self, container: ops.Container) -> None:
        """Create the directories holding the override files."""
        parents = {
            os.path.dirname(path)
            for path in (self.spec.dropin_path, self.spec.policy_dropin_path)
            if path
        }
        for parent in sorted(parents):
            if container.exists(parent):
                continue
            logger.debug("creating %s", parent)
            container.make_dir(
                parent,
                user=self.spec.file_user or "root",
                group=self.spec.file_group or "root",
                make_parents=True,
            )

    def service_environment(self) -> dict[str, str]:
        """Environment the service process reads its configuration from.

        Services that discover their configuration files through an
        environment variable (keystone's ``OS_KEYSTONE_CONFIG_FILES``) need
        the override drop-in in that list.  It is only listed while the file
        is in the container: ``oslo.config`` refuses to start when a file
        listed there is missing.  An override that fails validation is not
        applied and does not touch the drop-in, so the environment keeps
        listing whatever is being read at the time.
        """
        spec = self.spec
        if not spec.service_env:
            return {}
        files = [spec.main_conf]
        if spec.dropin_path and self._dropin_written():
            files.append(spec.dropin_path)
        return {spec.service_env: ";".join(files)}

    def _dropin_written(self) -> bool:
        """Whether the override drop-in is present in the container."""
        container = self.container()
        path = self.spec.dropin_path
        if not path or container is None or not container.can_connect():
            return False
        return container.exists(path)

    def extra_configs(
        self,
        container: ops.Container | None = None,
    ) -> list[tuple[sunbeam_core.ContainerConfigFile, str | None]]:
        """Files to write for the overrides.

        Called from the pebble handler while it writes configuration files
        into the container.  ``None`` content means the file is removed.

        Only validated overrides are applied: content the service rejects
        is not written, and whatever was applied before stays in place
        until the resource is fixed or cleared.

        :param container: container the files are written to, defaulting to
            the service container detected from the spec.  The handlers of
            the other containers running the service pass their own
            container, so that every one of them gets the directories the
            override files are written to.
        """
        config = self.config_text()
        policy = self.policy_text()
        target = container if container is not None else self.container()
        if target is not None:
            self.ensure_directories(target)
        if config is None and policy is None:
            if not self.resources_attached():
                # Nothing is attached, or the resources are not readable
                # right now (juju replaces a resource file while the unit
                # restarts).  That is not a user clearing the overrides, so
                # whatever was applied stays in place and the drop-ins of a
                # unit that never had an override simply do not exist.
                logger.debug(
                    "no override resources attached; keeping the applied "
                    "overrides"
                )
                return []
            if not self._removal_requested:
                # Attached, but empty: either the user attached empty files
                # to clear the overrides, or juju is replacing a resource
                # file right now and the hook reads the half juju has not
                # written yet as empty.  Clearing is done explicitly, by
                # the apply action the CLI runs, so a hook keeps what is
                # applied.
                logger.debug(
                    "empty override resources without an apply; keeping "
                    "the applied overrides"
                )
                return []
            self._stored.applied_digest = None
            return self._removal_entries()
        if target is None:
            # Nothing can be written without a reachable container, and the
            # overrides must not be applied before they are validated.
            logger.debug("not applying config overrides: container not ready")
            return []
        result = self.validation()
        if not result.valid:
            logger.error("not applying config overrides: %s", result.summary())
            return []
        self._stored.applied_digest = self.digest()
        return self._content_entries(config, policy)

    def _removal_entries(
        self,
    ) -> list[tuple[sunbeam_core.ContainerConfigFile, str | None]]:
        """Files to remove when no overrides are configured."""
        files: list[tuple[sunbeam_core.ContainerConfigFile, str | None]] = []
        if self.spec.dropin_path:
            files.append((self.spec.container_config_file(), None))
        if self.spec.policy_dropin_path:
            files.append((self.spec.policy_container_config_file(), None))
        return files

    def _content_entries(
        self, config: str | None, policy: str | None
    ) -> list[tuple[sunbeam_core.ContainerConfigFile, str | None]]:
        """Files carrying the content of validated overrides."""
        files: list[tuple[sunbeam_core.ContainerConfigFile, str | None]] = []
        if config is not None:
            if self.spec.dropin_path:
                files.append(
                    (
                        self.spec.container_config_file(),
                        self.config_content(),
                    )
                )
        elif self.spec.dropin_path:
            files.append((self.spec.container_config_file(), None))
        if policy is not None:
            files.append(
                (
                    self.spec.policy_container_config_file(),
                    self.policy_content(),
                )
            )
        elif self.spec.policy_dropin_path:
            files.append((self.spec.policy_container_config_file(), None))
        return files

    # Validating the overrides

    def container_names(self) -> list[str]:
        """Names of the containers holding the service.

        A service can run in several containers (nova-api, nova-scheduler,
        ...): every handler rendering the main configuration file holds a
        copy of the service and gets the override files.
        """
        if self.spec.container:
            return [self.spec.container]
        names: list[str] = []
        handlers = getattr(self.charm, "pebble_handlers", [])
        for handler in handlers:
            paths = [config.path for config in handler.container_configs]
            if (
                self.spec.main_conf in paths
                and handler.container_name not in names
            ):
                names.append(handler.container_name)
        return names

    def container_name(self) -> str | None:
        """Name of the container holding the service."""
        names = self.container_names()
        return names[0] if names else None

    def container(self) -> ops.Container | None:
        """Container holding the service, ready to run commands."""
        for name in self.container_names():
            container = self.charm.unit.get_container(name)
            if container.can_connect():
                return container
        return None

    def expected_paths(self) -> list[str]:
        """Paths the overrides occupy for the attached content."""
        paths = []
        if self.spec.dropin_path:
            paths.append(self.spec.dropin_path)
        if self.spec.policy_dropin_path:
            paths.append(self.spec.policy_dropin_path)
        return paths

    def validation(self) -> ValidationResult:
        """Validation result for the attached override resources.

        The result is cached for the lifetime of the hook so that the
        validator runs at most once for given content, however many
        callers ask for it.
        """
        key = (self.config_text(), self.policy_text())
        if self._validation_cache is not None:
            cached_key, result = self._validation_cache
            if cached_key == key:
                return result
        result = self.validate()
        self._validation_cache = (key, result)
        return result

    def validate(
        self,
        config: str | None = None,
        policy: str | None = None,
        base_conf: str | None = None,
    ) -> ValidationResult:
        """Validate override file content inside the workload container.

        Without content the attached resources are validated.

        :param config: configuration override content, defaulting to the
            attached ``config-override`` resource.
        :param policy: policy override content, defaulting to the attached
            ``policy-override`` resource.
        :param base_conf: rendered base configuration to compare against,
            read from ``spec.main_conf`` in the container when unset.
        """
        if config is None and policy is None:
            config = self.config_text()
            policy = self.policy_text()
        if config is None and policy is None:
            return ValidationResult(
                valid=True,
                warnings=["no configuration overrides are configured"],
                checks={"overrides": "not configured"},
            )
        if policy is not None and not self.spec.policy_supported:
            return ValidationResult.failure(
                "policy overrides are not supported by this charm",
                check="policy",
            )

        container = self.container()
        if container is None:
            name = self.container_name()
            if name is None:
                return ValidationResult.failure(
                    f"cannot determine the container holding "
                    f"{self.spec.main_conf}",
                    check="container",
                )
            return ValidationResult.failure(
                f"container {name} is not ready to validate the overrides",
                check="container",
            )

        return self._run_validator(container, config, policy, base_conf)

    def validate_content(self, content: str) -> ValidationResult:
        """Validate raw file text as an override file of unknown kind.

        A single file is either the configuration override or the policy
        override and the caller cannot say which: INI files begin with a
        section header (``[...]`` after comments/blank lines) whereas policy
        files are YAML mappings.  The likely interpretation is tried first;
        the other is only attempted when the charm supports policy overrides,
        the content could not be classified unambiguously and it carries
        something to apply.
        """
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        first_line = lines[0] if lines else ""
        if first_line.startswith("[") or not self.spec.policy_supported:
            return self.validate(config=content)
        policy_result = self.validate(policy=content)
        if policy_result.valid:
            return policy_result
        if not lines:
            # Comments only: the file carries no rule and no option, so it is
            # not an override under either interpretation and the policy
            # failure describes the file itself.
            return policy_result
        config_result = self.validate(config=content)
        if config_result.valid:
            return config_result
        return policy_result

    def _run_validator(
        self,
        container: ops.Container,
        config: str | None,
        policy: str | None,
        base_conf: str | None,
    ) -> ValidationResult:
        """Run the validator script on the content inside the container."""
        try:
            self._push_validator(container)
            cmd = self._validator_command(config, policy, container, base_conf)
        except (ops.pebble.PathError, OSError) as exc:
            return ValidationResult.failure(
                f"cannot prepare the validation run: {exc}",
                check="container",
            )
        logger.info("validating configuration overrides with %s", cmd)
        try:
            process = container.exec(
                cmd, encoding="utf-8", timeout=self.spec.validation_timeout
            )
            out, err = process.wait_output()
            stdout, stderr = out or "", err or ""
        except ops.pebble.ExecError as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
        except (
            ops.pebble.ChangeError,
            ops.pebble.ConnectionError,
            TimeoutError,
            OSError,
        ) as exc:
            return ValidationResult.failure(
                f"the validation run failed: {exc}", check="container"
            )
        except Exception as exc:  # noqa: BLE001
            # Anything else is still reported as a failed validation run
            # rather than failing the action with a traceback.
            logger.debug("validation run failed", exc_info=True)
            return ValidationResult.failure(
                f"the validation run failed: {exc!r}", check="container"
            )
        try:
            return ValidationResult.from_validator_output(stdout, stderr)
        except ValueError as exc:
            return ValidationResult.failure(str(exc), check="container")

    def _push_validator(self, container: ops.Container) -> None:
        """Copy the validator script into the container once per hook."""
        name = getattr(container, "name", "")
        if name and name in self._validator_pushed:
            return
        source = Path(__file__).with_name(VALIDATOR_SCRIPT)
        container.push(
            VALIDATOR_REMOTE_PATH,
            source.read_bytes(),
            make_dirs=True,
            permissions=0o644,
        )
        if name:
            self._validator_pushed.add(name)

    def _push(
        self,
        container: ops.Container,
        path: str,
        content: str | None,
    ) -> None:
        """Write a candidate file into the container, or remove it."""
        if content is None:
            if container.exists(path):
                container.remove_path(path)
            return
        container.push(
            path,
            content,
            make_dirs=True,
            permissions=0o644,
        )

    def _validator_command(
        self,
        config: str | None,
        policy: str | None,
        container: ops.Container,
        base_conf: str | None,
    ) -> list[str]:
        """Command running the validator on the content."""
        cmd = ["python3", VALIDATOR_REMOTE_PATH]
        main_conf = self.spec.main_conf
        if config is not None:
            self._push(container, CANDIDATE_CONF_PATH, config)
            cmd += ["--config", CANDIDATE_CONF_PATH]
            if base_conf is not None:
                self._push(container, BASELINE_CONF_PATH, base_conf)
                main_conf = BASELINE_CONF_PATH
        if policy is not None:
            self._push(container, CANDIDATE_POLICY_PATH, policy)
            cmd += ["--policy", CANDIDATE_POLICY_PATH]
            if self.spec.policy_namespace:
                cmd += ["--policy-namespace", self.spec.policy_namespace]
        cmd += ["--main-conf", main_conf]
        cmd += ["--blocklist", json.dumps(list(self.spec.blocklist))]
        cmd += ["--overridable", json.dumps(list(self.spec.overridable))]
        for namespace in self.spec.namespace:
            cmd += ["--namespace", namespace]
        for prefix in self.spec.namespace_prefix:
            cmd += ["--namespace-prefix", prefix]
        for group in self.spec.exclude_group:
            cmd += ["--exclude-group", group]
        if self.spec.unknown_is_warning:
            cmd += ["--unknown-is-warning"]
        return cmd

    # Reporting

    def reconcile(self) -> dict[str, Any]:
        """Report what is attached and what is applied."""
        config = self.config_text()
        policy = self.policy_text()
        attached = config is not None or policy is not None
        info: dict[str, Any] = {
            "resource": self.spec.resource,
            "policy_resource": self.spec.policy_resource,
            "enabled": attached,
            "applied": False,
            "override_digest": self.digest() if attached else "",
            "applied_digest": self._stored.applied_digest or "",
            "config_checksum": (
                hashlib.sha256(config.encode()).hexdigest()
                if config is not None
                else ""
            ),
            "policy_checksum": (
                hashlib.sha256(policy.encode()).hexdigest()
                if policy is not None
                else ""
            ),
            "files": {},
        }
        container = self.container()
        if container is None:
            info["container_ready"] = False
            return info
        info["container_ready"] = True
        for path in self.expected_paths():
            info["files"][path] = container.exists(path)
        if attached:
            active_paths = [
                path
                for path, present in (
                    (self.spec.dropin_path, config is not None),
                    (self.spec.policy_dropin_path, policy is not None),
                )
                if path and present
            ]
            info["applied"] = self._stored.applied_digest == info[
                "override_digest"
            ] and all(info["files"].get(p, False) for p in active_paths)
        return info

    def handle_validate_action(self, event: ops.ActionEvent) -> None:
        """Run the validate-config-overrides action.

        The action accepts a ``content`` parameter holding the raw text of
        an override file to validate, so the CLI can validate before
        attaching it.  Without it the attached resources are validated.
        """
        content = str(event.params.get("content") or "")
        if content.strip():
            result = self.validate_content(content)
        else:
            result = self.validation()
        event.set_results(result.to_action_results())
        if not result.valid:
            event.fail(result.summary())

    def handle_apply_action(self, event: ops.ActionEvent) -> None:
        """Run the apply-config-overrides action.

        Validates the attached resources and applies them through the same
        configuration path the ``config-changed`` flow uses, so the drop-in
        files are written now.  Content that does not validate is never
        applied: the previous overrides stay in place, the unit blocks and
        the errors are reported through the action results.  The action is
        idempotent with a ``config-changed`` that already applied the same
        content.  The action waits for juju to finish writing the attached
        resources before it reads them, so a resource that is still being
        replaced is not taken for a clear.
        """
        self.wait_for_settled_resources()
        result = self.validation()
        # An explicit apply is what clears the overrides when the attached
        # resources are empty; hooks alone never remove the drop-ins.
        self.request_removal()
        self.charm.configure_charm(event)
        event.set_results(result.to_action_results())
        if not result.valid:
            event.fail(result.summary())
        elif not getattr(self.charm, "_configure_charm_completed", True):
            event.fail("Failed to apply the overrides, check logs for details")

    def handle_get_action(self, event: ops.ActionEvent) -> None:
        """Run the get-config-overrides action.

        Reports the override content in effect on this unit and the files
        it is written to.
        """
        info = self.reconcile()
        applied = [path for path, present in info["files"].items() if present]
        results = {
            "content": self.config_text() or "",
            "policy": self.policy_text() or "",
            "applied": json.dumps(applied),
            "resource": str(info["resource"]),
            "enabled": str(info["enabled"]).lower(),
            "applied-digest": str(info["applied_digest"]),
            "override-digest": str(info["override_digest"]),
            "config-checksum": str(info["config_checksum"]),
            "policy-checksum": str(info["policy_checksum"]),
            "container-ready": str(info.get("container_ready", False)).lower(),
            "files": json.dumps(info["files"], sort_keys=True),
        }
        event.set_results(results)

    def wire_actions(self) -> None:
        """Observe the override actions when the charm declares them."""
        actions = self.charm.meta.actions or {}
        if "validate-config-overrides" in actions:
            self.charm.framework.observe(
                self.charm.on["validate-config-overrides"].action,
                self.handle_validate_action,
            )
        if "apply-config-overrides" in actions:
            self.charm.framework.observe(
                self.charm.on["apply-config-overrides"].action,
                self.handle_apply_action,
            )
        if "get-config-overrides" in actions:
            self.charm.framework.observe(
                self.charm.on["get-config-overrides"].action,
                self.handle_get_action,
            )
