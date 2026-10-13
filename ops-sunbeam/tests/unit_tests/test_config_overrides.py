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

"""Tests for ops_sunbeam.config_overrides."""

import dataclasses
import json
import logging
from types import (
    SimpleNamespace,
)

import ops
import ops.testing
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_overrides as sunbeam_config_overrides
import ops_sunbeam.container_handlers as sunbeam_chandlers
import pytest

logger = logging.getLogger(__name__)

META = """
name: test-charm
summary: test charm
description: test charm
containers:
  glance-api:
    type: oci-image
  keystone:
    type: oci-image
  ceilometer:
    type: oci-image
resources:
  config-override:
    type: file
    filename: override.conf
  policy-override:
    type: file
    filename: override.yaml
"""

META_NO_POLICY = """
name: test-charm
summary: test charm
description: test charm
containers:
  glance-api:
    type: oci-image
  keystone:
    type: oci-image
  ceilometer:
    type: oci-image
resources:
  config-override:
    type: file
    filename: override.conf
"""

ACTIONS = """
validate-config-overrides:
  description: Validate the configured overrides.
  params:
    content:
      type: string
      default: ""
      description: Override file content to validate.
apply-config-overrides:
  description: Validate the attached overrides and apply them now.
get-config-overrides:
  description: Report the configured overrides.
"""

GLANCE_SPEC = sunbeam_config_overrides.ConfigOverrideSpec(
    main_conf="/etc/glance/glance-api.conf",
    container="glance-api",
    dropin_path="/etc/glance/glance-api.conf.d/zz-config-overrides.conf",
    policy_dropin_path="/etc/glance/policy.d/zz-config-overrides.yaml",
    policy_namespace="glance",
    namespace=("glance.api",),
    exclude_group=("keystone_authtoken",),
    blocklist=("database/connection",),
    file_user="glance",
    file_group="glance",
)

KEYSTONE_SPEC = dataclasses.replace(
    GLANCE_SPEC,
    main_conf="/etc/keystone/keystone.conf",
    container="keystone",
    dropin_path="/etc/keystone/keystone.conf.d/zz-config-overrides.conf",
    policy_dropin_path=None,
    policy_namespace=None,
    namespace=("keystone",),
    namespace_prefix=("oslo",),
    service_env="OS_KEYSTONE_CONFIG_FILES",
)

CONFIG_TEXT = "[DEFAULT]\ndebug = true\n"
POLICY_TEXT = "get_image: role:admin\n"

GLANCE_CONVENTION = sunbeam_config_overrides.spec_from_convention(
    "/etc/glance/glance-api.conf",
    "glance",
    file_user="glance",
    file_group="glance",
    policy=True,
)


class OverrideCharm(ops.CharmBase):
    """Charm wiring the manager the way a real charm does."""

    spec = GLANCE_SPEC

    def __init__(self, framework: ops.Framework) -> None:
        """Run constructor."""
        super().__init__(framework)
        self.config_overrides = sunbeam_config_overrides.ConfigOverrideManager(
            self, self.spec
        )
        self.config_overrides.wire_actions()

    def configure_charm(self, event: ops.EventBase) -> None:
        """Reconcile like the real charm: write the override drop-ins.

        Stands in for the pebble handler's ``write_config`` path, which the
        apply action drives through ``configure_charm``.
        """
        self._configure_charm_completed = True
        container = self.config_overrides.container()
        if container is None:
            return
        for config, contents in self.config_overrides.extra_configs(container):
            if contents is None:
                if container.exists(config.path):
                    container.remove_path(config.path)
                continue
            container.push(config.path, contents, make_dirs=True)


class KeystoneOverrideCharm(OverrideCharm):
    """Charm with a WSGI service spec."""

    spec = KEYSTONE_SPEC


class ConventionAPICharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """API charm declaring the naming convention of its service."""

    service_name = "glance-api"
    config_override_namespace = "glance"
    wsgi_admin_script = "/bin/wsgi_admin"
    wsgi_public_script = "/bin/wsgi_public"

    @property
    def service_conf(self) -> str:
        """Configuration file rendered by the charm."""
        return "/etc/glance/glance-api.conf"

    @property
    def service_user(self) -> str:
        """Service user owning the override files."""
        return "glance"

    @property
    def service_group(self) -> str:
        """Service group owning the override files."""
        return "glance"


class NoOverridesAPICharm(ConventionAPICharm):
    """API charm that does not accept configuration overrides."""

    config_override_namespace = None


class NoPolicyAPICharm(ConventionAPICharm):
    """API charm whose service does not read the drop-in policy file."""

    config_override_policy = False


class MultiContainerAPICharm(ConventionAPICharm):
    """API charm serving one service from two containers."""

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            *super().get_pebble_handlers(),
            SecondaryWSGIHandler(self, "keystone"),
        ]


class ExtraContainerAPICharm(ConventionAPICharm):
    """API charm with a container not rendering the service config."""

    def get_pebble_handlers(self) -> list[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            *super().get_pebble_handlers(),
            SecondaryWSGIHandler(self, "keystone", []),
        ]


class EnvironmentOverrideCharm(ConventionAPICharm):
    """API charm whose service reads its configuration from an env var."""

    config_override_service_env = "OS_KEYSTONE_CONFIG_FILES"


class ConventionK8SCharm(sunbeam_charm.OSBaseOperatorCharmK8S):
    """K8s charm serving a service with no API, naming its namespace."""

    service_name = "ceilometer"
    config_override_namespace = "ceilometer"


class StubPebbleHandler:
    """Stand in for a pebble handler used by the container detection."""

    def __init__(self, container_name: str, paths: list[str]) -> None:
        """Run constructor."""
        self.container_name = container_name
        self.container_configs = [SimpleNamespace(path=path) for path in paths]


class SecondaryWSGIHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Second WSGI service handler of a charm.

    ``PebbleHandler`` objects are unnamed, so a charm can hold one
    instance per handler class: charms serving one service from several
    containers use one subclass per container.
    """

    def __init__(self, charm, container_name, container_configs=None):
        """Run constructor."""
        super().__init__(
            charm,
            container_name,
            charm.service_name,
            (
                charm.container_configs
                if container_configs is None
                else container_configs
            ),
            charm.template_dir,
            charm.configure_charm,
            f"wsgi-{charm.service_name}",
        )


class FakeContainer:
    """Stand in for ops.Container recording what is pushed to it."""

    def __init__(self) -> None:
        """Run constructor."""
        self.pushed: dict[str, str] = {}
        self.removed: list[str] = []
        self.made_dirs: list[tuple[str, str | None, str | None]] = []

    def can_connect(self) -> bool:
        """Whether the container is reachable."""
        return True

    def exists(self, path: str) -> bool:
        """Whether the path was pushed to this container."""
        return path in self.pushed

    def push(self, path: str, content: str, **kwargs: object) -> None:
        """Record a pushed file."""
        self.pushed[path] = content

    def make_dir(self, path: str, **kwargs: object) -> None:
        """Record a created directory."""
        self.made_dirs.append((path, kwargs.get("user"), kwargs.get("group")))

    def remove_path(self, path: str) -> None:
        """Record a removed file."""
        self.removed.append(path)


def make_harness(
    charm_cls: type[OverrideCharm] = OverrideCharm,
    meta: str = META,
) -> ops.testing.Harness:
    """Harness for a charm using config overrides."""
    harness = ops.testing.Harness(charm_cls, meta=meta, actions=ACTIONS)
    harness.begin()
    return harness


def run_action(
    harness: ops.testing.Harness,
    name: str,
    params: dict[str, str] | None = None,
) -> tuple[ops.testing.ActionFailed | None, dict[str, str]]:
    """Run an action, returning the failure and any results."""
    try:
        output = harness.run_action(name, params or {})
    except ops.testing.ActionFailed as exc:
        results = getattr(exc, "results", None) or getattr(
            getattr(exc, "output", None), "results", None
        )
        return exc, dict(results or {})
    return None, dict(getattr(output, "results", output))


VALIDATOR_OK = {
    "valid": True,
    "errors": [],
    "warnings": [],
    "checks": {"syntax": "ok"},
}


def allow_validation(
    harness: ops.testing.Harness,
    container: str = "glance-api",
    result: dict | None = None,
) -> None:
    """Make the in container validator report the given result."""
    harness.set_can_connect(container, True)
    harness.handle_exec(
        container,
        ["python3"],
        result=json.dumps(VALIDATOR_OK if result is None else result),
    )


class TestResources:
    """Tests for the resource driven override content."""

    def test_config_text_reads_the_resource(self):
        """The attached resource content is the override content."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)

        manager = harness.charm.config_overrides

        assert manager.config_text() == CONFIG_TEXT
        assert manager.policy_text() is None
        assert manager.config_content() == (
            sunbeam_config_overrides.DROPIN_HEADER + CONFIG_TEXT
        )
        assert manager.attached() is True

    def test_undeclared_resource_is_ignored(self):
        """A resource the charm does not declare is never fetched."""
        harness = make_harness(meta=META_NO_POLICY)
        harness.add_resource("config-override", CONFIG_TEXT)

        manager = harness.charm.config_overrides

        assert manager.config_text() == CONFIG_TEXT
        assert manager.policy_text() is None
        assert manager.attached() is True

    def test_policy_text_reads_the_resource(self):
        """The policy resource content is the policy override content."""
        harness = make_harness()
        harness.add_resource("policy-override", POLICY_TEXT)

        manager = harness.charm.config_overrides

        assert manager.policy_text() == POLICY_TEXT
        assert manager.policy_content() == (
            sunbeam_config_overrides.DROPIN_HEADER + POLICY_TEXT
        )
        assert manager.config_text() is None

    def test_missing_resource_is_no_override(self):
        """A resource that is not attached is no override, not an error."""
        manager = make_harness().charm.config_overrides

        assert manager.config_text() is None
        assert manager.policy_text() is None
        assert manager.config_content() is None
        assert manager.policy_content() is None
        assert manager.attached() is False

    def test_empty_resource_is_no_override(self):
        """An empty resource file is no override."""
        harness = make_harness()
        harness.add_resource("config-override", "")
        harness.add_resource("policy-override", "")

        manager = harness.charm.config_overrides

        assert manager.config_text() is None
        assert manager.policy_text() is None
        assert manager.attached() is False

    def test_digest_changes_with_content(self):
        """The digest identifies the override content."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        first = harness.charm.config_overrides.digest()

        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        second = harness.charm.config_overrides.digest()

        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)

        assert first != second
        assert harness.charm.config_overrides.digest() == first


class TestSpec:
    """Tests for ConfigOverrideSpec."""

    def test_requires_a_destination(self):
        """A spec without any destination is a programming error."""
        with pytest.raises(ValueError, match="dropin_path"):
            sunbeam_config_overrides.ConfigOverrideSpec(
                main_conf="/etc/glance/glance-api.conf"
            )

    def test_container_config_files(self):
        """Drop-in files carry the ownership from the spec."""
        config = GLANCE_SPEC.container_config_file()
        assert config.path == GLANCE_SPEC.dropin_path
        assert (config.user, config.group) == ("glance", "glance")

    def test_policy_support(self):
        """Keystone policy overrides are not supported."""
        assert GLANCE_SPEC.policy_supported
        assert not KEYSTONE_SPEC.policy_supported

    def test_resource_names(self):
        """The resources carry the contract names by default."""
        assert GLANCE_SPEC.resource == "config-override"
        assert GLANCE_SPEC.policy_resource == "policy-override"

    def test_dropin_dir(self):
        """The drop-in directory holds the override files of the service."""
        assert GLANCE_SPEC.dropin_dir == "/etc/glance/glance-api.conf.d"
        assert KEYSTONE_SPEC.dropin_dir == "/etc/keystone/keystone.conf.d"

    def test_dropin_dir_without_config_dropin(self):
        """A policy only spec names no configuration drop-in directory."""
        spec = dataclasses.replace(GLANCE_SPEC, dropin_path=None)

        assert spec.dropin_dir is None


class TestConvention:
    """Tests for the spec derived from the charm naming conventions."""

    def test_glance_paths(self):
        """The derived paths are the ones glance-k8s uses."""
        assert GLANCE_CONVENTION.main_conf == "/etc/glance/glance-api.conf"
        assert GLANCE_CONVENTION.dropin_path == (
            "/etc/glance/glance-api.conf.d/zz-config-overrides.conf"
        )
        assert GLANCE_CONVENTION.policy_dropin_path == (
            "/etc/glance/policy.d/zz-config-overrides.yaml"
        )
        assert GLANCE_CONVENTION.policy_namespace == "glance"
        assert GLANCE_CONVENTION.namespace_prefix == ("glance",)
        assert GLANCE_CONVENTION.blocklist == ("database/connection",)
        assert GLANCE_CONVENTION.container is None
        assert (GLANCE_CONVENTION.file_user, GLANCE_CONVENTION.file_group) == (
            "glance",
            "glance",
        )

    def test_keystone_paths(self):
        """The derived paths are the ones keystone-k8s uses."""
        spec = sunbeam_config_overrides.spec_from_convention(
            "/etc/keystone/keystone.conf", "keystone", policy=True
        )

        assert spec.dropin_path == (
            "/etc/keystone/keystone.conf.d/zz-config-overrides.conf"
        )
        assert spec.policy_dropin_path == (
            "/etc/keystone/policy.d/zz-config-overrides.yaml"
        )
        assert spec.policy_namespace == "keystone"

    def test_policy_paths_require_opt_in(self):
        """Policy paths are only derived for a service reading policy.d."""
        spec = sunbeam_config_overrides.spec_from_convention(
            "/etc/nova/nova.conf", "nova"
        )

        assert spec.dropin_path == (
            "/etc/nova/nova.conf.d/zz-config-overrides.conf"
        )
        assert spec.policy_dropin_path is None
        assert spec.policy_namespace is None
        assert not spec.policy_supported

    def test_charm_without_policy_has_no_policy_paths(self):
        """A service not reading policy.d rejects policy overrides."""
        harness = make_harness(NoPolicyAPICharm)

        spec = harness.charm.config_override_spec
        assert spec is not None
        assert spec.dropin_path == GLANCE_CONVENTION.dropin_path
        assert not spec.policy_supported

    def test_charm_without_namespace_has_no_spec(self):
        """A charm not naming a namespace does not accept overrides."""
        harness = make_harness(NoOverridesAPICharm)

        assert harness.charm.config_override_spec is None

    def test_charm_spec_follows_the_convention(self):
        """A charm naming a namespace gets the convention spec."""
        harness = make_harness(ConventionAPICharm)

        assert harness.charm.config_override_spec == GLANCE_CONVENTION

    def test_charm_spec_follows_the_service_environment(self):
        """The environment variable named by the charm reaches the spec."""
        harness = make_harness(EnvironmentOverrideCharm)

        spec = harness.charm.config_override_spec
        assert spec is not None
        assert spec.service_env == "OS_KEYSTONE_CONFIG_FILES"

    def test_charm_without_api_gets_the_convention_spec(self):
        """A service with no API derives its spec from the service name."""
        harness = make_harness(ConventionK8SCharm)

        spec = harness.charm.config_override_spec
        assert spec is not None
        assert spec.main_conf == "/etc/ceilometer/ceilometer.conf"
        assert spec.dropin_path == (
            "/etc/ceilometer/ceilometer.conf.d/zz-config-overrides.conf"
        )
        assert (spec.file_user, spec.file_group) == (
            "ceilometer",
            "ceilometer",
        )
        assert harness.charm.config_override_dir == (
            "/etc/ceilometer/ceilometer.conf.d"
        )

    def test_charm_without_namespace_has_no_dropin_dir(self):
        """A charm not accepting overrides names no drop-in directory."""
        harness = make_harness(NoOverridesAPICharm)

        assert harness.charm.config_override_dir is None


class TestServiceContainers:
    """Detection of the containers a service runs in."""

    def test_container_names_cover_every_handler_rendering_main_conf(self):
        """Every container whose handler renders the config holds a copy."""
        harness = make_harness(ConventionAPICharm)
        main_conf = GLANCE_CONVENTION.main_conf
        harness.charm.pebble_handlers = [
            StubPebbleHandler(
                "glance-api", [main_conf, "/etc/glance/api-paste.ini"]
            ),
            StubPebbleHandler("glance-scheduler", ["/etc/glance/other.conf"]),
            StubPebbleHandler("glance-worker", [main_conf]),
            StubPebbleHandler("glance-worker", [main_conf]),
        ]
        manager = harness.charm.config_overrides

        assert manager.container_names() == ["glance-api", "glance-worker"]
        assert manager.container_name() == "glance-api"

    def test_container_names_follow_the_spec_container(self):
        """A spec naming a container covers that container only."""
        harness = make_harness()
        harness.charm.pebble_handlers = [
            StubPebbleHandler("glance-worker", [GLANCE_SPEC.main_conf])
        ]
        manager = harness.charm.config_overrides

        assert manager.container_names() == ["glance-api"]

    def test_container_uses_a_reachable_service_container(self):
        """A down container does not stop the others from validating."""
        harness = make_harness(ConventionAPICharm)
        main_conf = GLANCE_CONVENTION.main_conf
        harness.charm.pebble_handlers = [
            StubPebbleHandler("glance-api", [main_conf]),
            StubPebbleHandler("keystone", [main_conf]),
        ]
        harness.set_can_connect("keystone", True)
        manager = harness.charm.config_overrides

        container = manager.container()

        assert manager.container_name() == "glance-api"
        assert container is not None
        assert container.name == "keystone"

    def test_handler_extra_configs_applies_to_every_service_container(self):
        """A handler of any container the service runs in writes overrides."""
        harness = make_harness(MultiContainerAPICharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        manager.validate = lambda *a, **k: (  # type: ignore[method-assign]
            sunbeam_config_overrides.ValidationResult(valid=True)
        )
        handler = harness.charm.pebble_handlers[1]
        fake = FakeContainer()

        files = handler.extra_configs(fake)

        assert [config.path for config, _ in files] == [
            GLANCE_CONVENTION.dropin_path,
            GLANCE_CONVENTION.policy_dropin_path,
        ]
        written = {
            config.path: contents for config, contents in files if contents
        }
        assert written[GLANCE_CONVENTION.dropin_path].endswith(CONFIG_TEXT)
        assert sorted(p for p, _, _ in fake.made_dirs) == [
            "/etc/glance/glance-api.conf.d",
            "/etc/glance/policy.d",
        ]

    def test_handler_extra_configs_skips_unrelated_containers(self):
        """A container not rendering the service config writes nothing."""
        harness = make_harness(ExtraContainerAPICharm)
        handler = harness.charm.pebble_handlers[1]
        fake = FakeContainer()

        assert handler.extra_configs(fake) == []
        assert fake.made_dirs == []

    def test_handler_extra_configs_without_namespace(self):
        """A charm not naming a namespace writes nothing."""
        harness = make_harness(NoOverridesAPICharm)
        handler = harness.charm.pebble_handlers[0]
        fake = FakeContainer()

        assert handler.extra_configs(fake) == []
        assert fake.made_dirs == []


class TestServiceEnvironment:
    """Environment a service reads its configuration files from."""

    def test_without_a_service_env(self):
        """A service taking its files on the command line gets none."""
        manager = make_harness().charm.config_overrides

        assert manager.service_environment() == {}

    def test_without_overrides(self):
        """Only the file the charm renders is listed with no overrides."""
        harness = make_harness(EnvironmentOverrideCharm)

        assert harness.charm.config_overrides.service_environment() == {
            "OS_KEYSTONE_CONFIG_FILES": "/etc/glance/glance-api.conf"
        }

    def test_with_applied_config(self):
        """The drop-in is listed while it is present in the container."""
        harness = make_harness(EnvironmentOverrideCharm)
        manager = harness.charm.config_overrides
        manager.container = (  # type: ignore[method-assign]
            self._container_with_dropin
        )

        assert manager.service_environment() == {
            "OS_KEYSTONE_CONFIG_FILES": (
                "/etc/glance/glance-api.conf;"
                "/etc/glance/glance-api.conf.d/zz-config-overrides.conf"
            )
        }

    def test_lists_no_policy_file(self):
        """The policy drop-in is not one of the configuration files."""
        harness = make_harness(EnvironmentOverrideCharm)
        manager = harness.charm.config_overrides
        container = self._container_with_dropin()
        container.push(GLANCE_CONVENTION.policy_dropin_path, POLICY_TEXT)
        manager.container = lambda: container  # type: ignore[method-assign]

        value = manager.service_environment()["OS_KEYSTONE_CONFIG_FILES"]

        assert value.split(";") == [
            "/etc/glance/glance-api.conf",
            GLANCE_CONVENTION.dropin_path,
        ]

    def test_drops_the_dropin_when_rejected(self):
        """Rejected content is not listed: its drop-in is never written."""
        harness = make_harness(EnvironmentOverrideCharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        manager.validate = lambda *a, **k: (  # type: ignore[method-assign]
            sunbeam_config_overrides.ValidationResult(
                valid=False, errors=["syntax error"]
            )
        )
        manager.container = lambda: FakeContainer()  # type: ignore[method-assign]

        assert manager.service_environment() == {
            "OS_KEYSTONE_CONFIG_FILES": "/etc/glance/glance-api.conf"
        }

    def test_keeps_the_dropin_when_new_content_is_rejected(self):
        """Rejected content leaves the applied overrides being read."""
        harness = make_harness(EnvironmentOverrideCharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        manager.validate = lambda *a, **k: (  # type: ignore[method-assign]
            sunbeam_config_overrides.ValidationResult(
                valid=False, errors=["syntax error"]
            )
        )
        manager.container = (  # type: ignore[method-assign]
            self._container_with_dropin
        )

        assert manager.service_environment() == {
            "OS_KEYSTONE_CONFIG_FILES": (
                "/etc/glance/glance-api.conf;"
                "/etc/glance/glance-api.conf.d/zz-config-overrides.conf"
            )
        }

    def test_without_config_content(self):
        """Policy-only content removes the configuration drop-in."""
        harness = make_harness(EnvironmentOverrideCharm)
        harness.add_resource("policy-override", POLICY_TEXT)
        manager = harness.charm.config_overrides
        manager.validate = lambda *a, **k: (  # type: ignore[method-assign]
            sunbeam_config_overrides.ValidationResult(valid=True)
        )
        manager.container = lambda: FakeContainer()  # type: ignore[method-assign]

        assert manager.service_environment() == {
            "OS_KEYSTONE_CONFIG_FILES": "/etc/glance/glance-api.conf"
        }
        assert [
            (config.path, content)
            for config, content in manager.extra_configs()
        ] == [
            (GLANCE_CONVENTION.dropin_path, None),
            (
                GLANCE_CONVENTION.policy_dropin_path,
                sunbeam_config_overrides.DROPIN_HEADER + POLICY_TEXT,
            ),
        ]

    @staticmethod
    def _container_with_dropin() -> FakeContainer:
        """Container holding an applied configuration drop-in."""
        container = FakeContainer()
        container.push(GLANCE_CONVENTION.dropin_path, CONFIG_TEXT)
        return container

    def test_wsgi_layer_carries_the_environment(self):
        """The pebble layer runs the service with the environment set."""
        harness = make_harness(EnvironmentOverrideCharm)
        harness.charm.config_overrides.container = (  # type: ignore[method-assign]
            self._container_with_dropin
        )
        handler = harness.charm.pebble_handlers[0]

        layer = handler.get_layer()

        assert layer["services"][handler.wsgi_service_name]["environment"][
            "OS_KEYSTONE_CONFIG_FILES"
        ] == (
            "/etc/glance/glance-api.conf;"
            "/etc/glance/glance-api.conf.d/zz-config-overrides.conf"
        )


class TestValidationResult:
    """Tests for the validation result."""

    def test_from_validator_output(self):
        """The validator JSON is parsed into a result."""
        output = json.dumps(
            {
                "valid": False,
                "errors": ["api/workers is not a known option"],
                "warnings": ["namespace oslo.db not available"],
                "checks": {"syntax": "ok", "schema": "failed"},
                "schema": {"unknown_options": ["api/workers"]},
            }
        )

        result = (
            sunbeam_config_overrides.ValidationResult.from_validator_output(
                output
            )
        )

        assert result.valid is False
        assert result.errors == ["api/workers is not a known option"]
        assert result.warnings == ["namespace oslo.db not available"]
        assert result.checks["schema"] == "failed"
        assert result.details["schema"] == {"unknown_options": ["api/workers"]}
        assert result.raw == output
        assert result.summary() == "api/workers is not a known option"

    def test_from_validator_output_rejects_noise(self):
        """Output that is not the validator JSON is an error."""
        with pytest.raises(ValueError, match="did not report a result"):
            sunbeam_config_overrides.ValidationResult.from_validator_output(
                "Traceback (most recent call last): ...", "boom"
            )

    def test_action_results(self):
        """Results are strings for the action output."""
        result = sunbeam_config_overrides.ValidationResult(
            valid=True,
            warnings=["no configuration overrides are configured"],
            checks={"overrides": "not configured"},
        )

        results = result.to_action_results()

        assert results["valid"] == "true"
        assert json.loads(results["errors"]) == []
        assert json.loads(results["warnings"]) == [
            "no configuration overrides are configured"
        ]
        assert json.loads(results["checks"]) == {"overrides": "not configured"}


class TestManager:
    """Tests for ConfigOverrideManager."""

    def test_extra_configs_when_not_configured(self):
        """Without attached resources the drop-in files are removed."""
        harness = make_harness()
        manager = harness.charm.config_overrides

        files = manager.extra_configs()

        assert [(config.path, contents) for config, contents in files] == [
            (GLANCE_SPEC.dropin_path, None),
            (GLANCE_SPEC.policy_dropin_path, None),
        ]

    def test_extra_configs_write_content(self):
        """Attached content is written to the drop-in files once it is valid."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        allow_validation(harness)
        manager = harness.charm.config_overrides

        files = manager.extra_configs()

        written = {
            config.path: contents for config, contents in files if contents
        }
        assert written[GLANCE_SPEC.dropin_path].endswith(CONFIG_TEXT)
        assert written[GLANCE_SPEC.dropin_path].startswith(
            sunbeam_config_overrides.DROPIN_HEADER
        )
        assert written[GLANCE_SPEC.policy_dropin_path].endswith(POLICY_TEXT)

    def test_extra_configs_policy_only_removes_config_dropin(self):
        """A policy-only override marks the config drop-in for removal."""
        harness = make_harness()
        harness.add_resource("policy-override", POLICY_TEXT)
        allow_validation(harness)
        manager = harness.charm.config_overrides

        files = manager.extra_configs()

        assert [(config.path, contents) for config, contents in files] == [
            (GLANCE_SPEC.dropin_path, None),
            (
                GLANCE_SPEC.policy_dropin_path,
                sunbeam_config_overrides.DROPIN_HEADER + POLICY_TEXT,
            ),
        ]

    def test_extra_configs_creates_directories(self):
        """The directories holding the override files are created."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        manager = harness.charm.config_overrides
        manager.validate = lambda *a, **k: (  # type: ignore[method-assign]
            sunbeam_config_overrides.ValidationResult(valid=True)
        )
        fake = FakeContainer()
        manager.container = lambda: fake  # type: ignore[method-assign]

        manager.extra_configs()

        assert sorted(p for p, _, _ in fake.made_dirs) == [
            "/etc/glance/glance-api.conf.d",
            "/etc/glance/policy.d",
        ]
        assert {p: (u, g) for p, u, g in fake.made_dirs} == {
            "/etc/glance/glance-api.conf.d": ("glance", "glance"),
            "/etc/glance/policy.d": ("glance", "glance"),
        }

    def test_extra_configs_creates_directories_in_given_container(self):
        """The directories are created in the caller's container."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.charm.config_overrides.validate = lambda *a, **k: (
            sunbeam_config_overrides.ValidationResult(valid=True)
        )  # type: ignore[method-assign]
        fake = FakeContainer()

        files = harness.charm.config_overrides.extra_configs(fake)

        written = {
            config.path: contents for config, contents in files if contents
        }
        assert written[GLANCE_SPEC.dropin_path].endswith(CONFIG_TEXT)
        assert sorted(p for p, _, _ in fake.made_dirs) == [
            "/etc/glance/glance-api.conf.d",
            "/etc/glance/policy.d",
        ]
        assert {p: (u, g) for p, u, g in fake.made_dirs} == {
            "/etc/glance/glance-api.conf.d": ("glance", "glance"),
            "/etc/glance/policy.d": ("glance", "glance"),
        }

    def test_extra_configs_creates_directories_without_content(self):
        """The drop-in directories exist even with no overrides."""
        harness = make_harness()
        manager = harness.charm.config_overrides
        fake = FakeContainer()
        manager.container = lambda: fake  # type: ignore[method-assign]

        files = manager.extra_configs()

        assert [config.path for config, _ in files] == [
            GLANCE_SPEC.dropin_path,
            GLANCE_SPEC.policy_dropin_path,
        ]
        assert sorted(p for p, _, _ in fake.made_dirs) == [
            "/etc/glance/glance-api.conf.d",
            "/etc/glance/policy.d",
        ]

    def test_extra_configs_without_container_creates_nothing(self):
        """No directory is created when the container is unreachable."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        created: list[object] = []
        manager.ensure_directories = created.append  # type: ignore[method-assign]
        manager.container = lambda: None  # type: ignore[method-assign]

        assert manager.extra_configs() == []
        assert created == []

    def test_extra_configs_skips_rejected_content(self):
        """Content the service rejects is not applied."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(
            harness,
            result={
                "valid": False,
                "errors": ["database/connection is blocklisted"],
                "warnings": [],
                "checks": {"blocklist": "failed"},
            },
        )
        manager = harness.charm.config_overrides

        assert manager.extra_configs() == []
        assert manager.reconcile()["applied"] is False

    def test_extra_configs_without_container(self):
        """Overrides are not applied before the container is reachable."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides

        assert manager.extra_configs() == []

    def test_validation_is_cached_per_content(self):
        """The validator runs once per attached content."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        calls: list[tuple[str | None, str | None]] = []

        def fake_validate(
            config: str | None = None,
            policy: str | None = None,
            base_conf: str | None = None,
        ) -> sunbeam_config_overrides.ValidationResult:
            calls.append((config, policy))
            return sunbeam_config_overrides.ValidationResult(valid=True)

        manager.validate = fake_validate  # type: ignore[method-assign]

        assert manager.validation().valid is True
        manager.validation()
        assert len(calls) == 1

        # Resource content is immutable once fetched: attaching the policy
        # resource is what changes the validated content.
        harness.add_resource("policy-override", POLICY_TEXT)
        manager.validation()
        assert len(calls) == 2

    def test_extra_configs_reject_unsupported_policy(self):
        """Content with policy is rejected when the charm lacks support."""
        harness = make_harness(KeystoneOverrideCharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        allow_validation(harness, "keystone")
        manager = harness.charm.config_overrides

        assert manager.extra_configs() == []
        result = manager.validation()
        assert result.valid is False
        assert "policy" in result.errors[0]

    def test_extra_configs_without_policy_section(self):
        """Config-only content applies without policy support."""
        harness = make_harness(KeystoneOverrideCharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(harness, "keystone")
        manager = harness.charm.config_overrides

        files = manager.extra_configs()

        assert [config.path for config, _ in files] == [
            KEYSTONE_SPEC.dropin_path
        ]

    def test_validate_without_resources(self):
        """Nothing attached is not a failure."""
        manager = make_harness().charm.config_overrides

        result = manager.validate()

        assert result.valid is True
        assert result.checks == {"overrides": "not configured"}

    def test_validate_rejects_policy_when_unsupported(self):
        """Policy overrides are rejected when the charm cannot apply them."""
        harness = make_harness(KeystoneOverrideCharm)
        harness.add_resource("policy-override", POLICY_TEXT)
        manager = harness.charm.config_overrides

        result = manager.validate()

        assert result.valid is False
        assert result.checks == {"policy": "failed"}
        assert "policy overrides are not supported" in result.errors[0]

    def test_validate_container_not_ready(self):
        """Validation needs the workload container."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides

        result = manager.validate()

        assert result.valid is False
        assert result.checks == {"container": "failed"}
        assert "not ready" in result.errors[0]

    def test_validate_parses_validator_output(self):
        """The validator output is parsed and reported."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.set_can_connect("glance-api", True)
        expected = {
            "valid": False,
            "errors": ["api/workers is not a known option"],
            "warnings": [],
            "checks": {"syntax": "ok", "schema": "failed"},
        }
        harness.handle_exec(
            "glance-api", ["python3"], result=json.dumps(expected)
        )
        manager = harness.charm.config_overrides

        result = manager.validate()

        assert result.valid is False
        assert result.errors == ["api/workers is not a known option"]
        assert result.checks["schema"] == "failed"

    def test_validate_reports_unparseable_output(self):
        """A validator run that prints nothing useful fails validation."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.set_can_connect("glance-api", True)
        harness.handle_exec("glance-api", ["python3"], result="Killed")
        manager = harness.charm.config_overrides

        result = manager.validate()

        assert result.valid is False
        assert result.checks == {"container": "failed"}

    def test_validator_command(self):
        """The command carries the service and policy metadata."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        manager = harness.charm.config_overrides
        container = FakeContainer()
        command = manager._validator_command(
            CONFIG_TEXT, POLICY_TEXT, container, None
        )

        assert command[0] == "python3"
        assert command[1] == sunbeam_config_overrides.VALIDATOR_REMOTE_PATH
        assert "--config" in command
        assert "--policy" in command
        assert command[command.index("--policy-namespace") + 1] == "glance"
        assert command[command.index("--main-conf") + 1] == (
            GLANCE_SPEC.main_conf
        )
        assert json.loads(command[command.index("--blocklist") + 1]) == [
            "database/connection"
        ]
        assert command[command.index("--namespace") + 1] == "glance.api"
        assert command[command.index("--exclude-group") + 1] == (
            "keystone_authtoken"
        )
        assert set(container.pushed) == {
            sunbeam_config_overrides.CANDIDATE_CONF_PATH,
            sunbeam_config_overrides.CANDIDATE_POLICY_PATH,
        }

    def test_validator_command_uses_base_conf(self):
        """A rendered base config is pushed and used for managed options."""
        harness = make_harness(KeystoneOverrideCharm)
        harness.add_resource("config-override", CONFIG_TEXT)
        manager = harness.charm.config_overrides
        container = FakeContainer()
        command = manager._validator_command(
            CONFIG_TEXT,
            None,
            container,
            "[DEFAULT]\nlog_file = /tmp/keystone.log\n",
        )

        assert command[command.index("--main-conf") + 1] == (
            sunbeam_config_overrides.BASELINE_CONF_PATH
        )
        assert (
            container.pushed[sunbeam_config_overrides.BASELINE_CONF_PATH]
            == "[DEFAULT]\nlog_file = /tmp/keystone.log\n"
        )
        assert command[command.index("--namespace-prefix") + 1] == "oslo"

    def test_reconcile_after_applying(self):
        """Reconcile reports the content as applied once the files are there."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.add_resource("policy-override", POLICY_TEXT)
        allow_validation(harness)
        manager = harness.charm.config_overrides
        files = manager.extra_configs()
        container = harness.charm.unit.get_container("glance-api")
        for config, contents in files:
            if contents is None:
                continue
            container.push(config.path, contents, make_dirs=True)

        info = manager.reconcile()

        assert info["enabled"] is True
        assert info["applied"] is True
        assert info["applied_digest"] == manager.digest()
        assert info["files"] == {
            GLANCE_SPEC.dropin_path: True,
            GLANCE_SPEC.policy_dropin_path: True,
        }
        assert info["config_checksum"] != ""

    def test_reconcile_reports_missing_files(self):
        """Files that were never written are reported as missing."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.set_can_connect("glance-api", True)
        manager = harness.charm.config_overrides
        manager.extra_configs()

        info = manager.reconcile()

        assert info["enabled"] is True
        assert info["applied"] is False
        assert info["files"] == {
            GLANCE_SPEC.dropin_path: False,
            GLANCE_SPEC.policy_dropin_path: False,
        }


class TestActions:
    """Tests for the charm actions."""

    def test_validate_action_without_overrides(self):
        """The validate action succeeds when nothing is configured."""
        harness = make_harness()

        failure, results = run_action(harness, "validate-config-overrides")

        assert failure is None
        assert results["valid"] == "true"
        assert json.loads(results["checks"]) == {"overrides": "not configured"}

    def test_validate_action_fails_when_container_not_ready(self):
        """The validate action fails while the workload is unavailable."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)

        failure, results = run_action(harness, "validate-config-overrides")

        assert failure is not None
        assert results["valid"] == "false"
        assert any(
            "not ready" in error for error in json.loads(results["errors"])
        )

    def test_validate_action_validates_passed_content(self):
        """The CLI validates raw file content before attaching it."""
        harness = make_harness()
        harness.set_can_connect("glance-api", True)
        harness.handle_exec(
            "glance-api",
            ["python3"],
            result=json.dumps(
                {
                    "valid": False,
                    "errors": ["database/connection is blocked"],
                    "warnings": [],
                    "checks": {"blocklist": "failed"},
                }
            ),
        )

        failure, results = run_action(
            harness,
            "validate-config-overrides",
            {"content": "[database]\nconnection = sqlite://\n"},
        )

        assert failure is not None
        assert results["valid"] == "false"
        assert json.loads(results["errors"]) == [
            "database/connection is blocked"
        ]
        assert json.loads(results["checks"]) == {"blocklist": "failed"}

    def test_validate_action_accepts_policy_content(self):
        """Raw policy text is accepted through the policy interpretation."""
        harness = make_harness()
        harness.set_can_connect("glance-api", True)

        def validator(args: ops.testing.ExecArgs) -> ops.testing.ExecResult:
            if "--policy" in args.command:
                return ops.testing.ExecResult(
                    0,
                    json.dumps(
                        {
                            "valid": True,
                            "errors": [],
                            "warnings": [],
                            "checks": {"policy": "ok"},
                        }
                    ),
                )
            return ops.testing.ExecResult(
                0,
                json.dumps(
                    {
                        "valid": False,
                        "errors": ["not valid INI"],
                        "warnings": [],
                        "checks": {"syntax": "failed"},
                    }
                ),
            )

        harness.handle_exec("glance-api", ["python3"], handler=validator)

        failure, results = run_action(
            harness, "validate-config-overrides", {"content": POLICY_TEXT}
        )

        assert failure is None
        assert results["valid"] == "true"
        assert json.loads(results["checks"]) == {"policy": "ok"}

    def test_validate_action_reports_validator_result(self):
        """The validate action exposes the validator result."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.set_can_connect("glance-api", True)
        harness.handle_exec(
            "glance-api",
            ["python3"],
            result=json.dumps(
                {
                    "valid": True,
                    "errors": [],
                    "warnings": ["api/workers is deprecated"],
                    "checks": {"syntax": "ok"},
                }
            ),
        )

        failure, results = run_action(harness, "validate-config-overrides")

        assert failure is None
        assert results["valid"] == "true"
        assert json.loads(results["warnings"]) == ["api/workers is deprecated"]

    def test_get_action_reports_state(self):
        """The get action reports the content and where it is applied."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        harness.set_can_connect("glance-api", True)

        failure, results = run_action(harness, "get-config-overrides")

        assert failure is None
        assert results["content"] == CONFIG_TEXT
        assert results["enabled"] == "true"
        assert json.loads(results["applied"]) == []
        assert results["override-digest"] != ""
        assert results["config-checksum"] != ""
        assert results["policy-checksum"] == ""
        assert json.loads(results["files"]) == {
            GLANCE_SPEC.dropin_path: False,
            GLANCE_SPEC.policy_dropin_path: False,
        }

    def test_get_action_lists_applied_files(self):
        """Files carrying the override are listed as applied."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(harness)
        manager = harness.charm.config_overrides
        container = harness.charm.unit.get_container("glance-api")
        for config, contents in manager.extra_configs():
            if contents is not None:
                container.push(config.path, contents, make_dirs=True)

        failure, results = run_action(harness, "get-config-overrides")

        assert failure is None
        assert json.loads(results["applied"]) == [GLANCE_SPEC.dropin_path]
        assert results["applied-digest"] == manager.digest()

    def test_apply_action_applies_attached_overrides(self):
        """The apply action writes the attached overrides now."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(harness)

        failure, results = run_action(harness, "apply-config-overrides")

        container = harness.charm.unit.get_container("glance-api")
        assert failure is None
        assert results["valid"] == "true"
        assert container.exists(GLANCE_SPEC.dropin_path)

    def test_apply_action_is_idempotent(self):
        """Applying again keeps the same drop-in in place."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(harness)
        run_action(harness, "apply-config-overrides")
        container = harness.charm.unit.get_container("glance-api")
        first = container.pull(GLANCE_SPEC.dropin_path).read()

        failure, _ = run_action(harness, "apply-config-overrides")

        assert failure is None
        assert container.pull(GLANCE_SPEC.dropin_path).read() == first

    def test_apply_action_reports_validation_errors(self):
        """Rejected content is not applied and fails the action."""
        harness = make_harness()
        harness.add_resource("config-override", CONFIG_TEXT)
        allow_validation(
            harness,
            result={
                "valid": False,
                "errors": ["database/connection is blocklisted"],
                "warnings": [],
                "checks": {"blocklist": "failed"},
            },
        )

        failure, results = run_action(harness, "apply-config-overrides")

        container = harness.charm.unit.get_container("glance-api")
        assert failure is not None
        assert results["valid"] == "false"
        assert not container.exists(GLANCE_SPEC.dropin_path)

    def test_apply_action_without_resources(self):
        """An empty resource clears the overrides without blocking."""
        harness = make_harness()
        harness.add_resource("config-override", "")
        harness.set_can_connect("glance-api", True)

        failure, results = run_action(harness, "apply-config-overrides")

        container = harness.charm.unit.get_container("glance-api")
        assert failure is None
        assert results["valid"] == "true"
        assert not container.exists(GLANCE_SPEC.dropin_path)
