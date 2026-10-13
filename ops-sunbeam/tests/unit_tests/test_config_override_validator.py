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

"""Tests for ops_sunbeam.config_override_validator."""

import json
import logging
import sys
import types

import ops_sunbeam.config_override_validator as validator
import pytest

logger = logging.getLogger(__name__)


class TestParseIni:
    """Tests for the INI parsing helpers."""

    def test_normalises_groups_and_options(self):
        """Group and option names are normalised for comparison."""
        sections, errors, warnings = validator.parse_ini(
            "[DEFAULT]\nDebug = true\n[FoO-Bar]\nsome-option = 1\n"
        )

        assert errors == []
        assert warnings == []
        assert sections == {
            "DEFAULT": {"debug": "true"},
            "foo_bar": {"some_option": "1"},
        }

    def test_default_section_is_not_merged(self):
        """[DEFAULT] values do not leak into other sections."""
        sections, errors, _ = validator.parse_ini(
            "[DEFAULT]\ndebug = true\n[api]\nworkers = 3\n"
        )

        assert errors == []
        assert "debug" not in sections["api"]
        assert sections["api"] == {"workers": "3"}

    def test_missing_section_header_is_a_syntax_error(self):
        """Options outside a section are a syntax error."""
        sections, errors, _ = validator.parse_ini("debug = true\n")

        assert sections == {}
        assert errors and "syntax error" in errors[0]

    def test_duplicate_options_warn(self):
        """Duplicates are accepted by oslo (last wins) but reported."""
        sections, errors, warnings = validator.parse_ini(
            "[api]\nworkers = 3\nworkers = 4\n"
        )

        assert errors == []
        assert sections == {"api": {"workers": "4"}}
        assert warnings == ["duplicate option api/workers (last value wins)"]


class TestOptionSets:
    """Tests for the blocklist and managed option checks."""

    def test_blocklist_and_managed_options(self):
        """Blocked options and charm owned options are both rejected."""
        candidate = {
            "database": {"connection": "sqlite://"},
            "api": {"workers": "3"},
        }
        errors, hits, conflicts = validator.check_option_sets(
            candidate, ["api/workers"], [], {"database": {"connection"}}
        )

        assert hits == ["api/workers"]
        assert conflicts == ["database/connection"]
        assert len(errors) == 2

    def test_managed_option_can_be_overridable(self):
        """An overridable rule removes the managed option conflict."""
        candidate = {"api": {"workers": "3"}}
        errors, hits, conflicts = validator.check_option_sets(
            candidate, [], ["api/workers"], {"api": {"workers"}}
        )

        assert (errors, hits, conflicts) == ([], [], [])

    def test_blocklist_wildcards(self):
        """Wildcards match whole sections and bare option names."""
        candidate = {
            "database": {"connection": "sqlite://", "max_pool_size": "5"},
            "api": {"workers": "3"},
        }
        _, hits, _ = validator.check_option_sets(
            candidate, ["database/*", "workers"], [], {}
        )

        assert sorted(hits) == [
            "api/workers",
            "database/connection",
            "database/max_pool_size",
        ]

    def test_managed_options_from_rendered_file(self, tmp_path):
        """Options present in the charm rendered file are managed."""
        main_conf = tmp_path / "glance-api.conf"
        main_conf.write_text("[DEFAULT]\nlog_file = /var/log/glance/api.log\n")

        managed = validator.managed_options(str(main_conf))

        assert managed == {"DEFAULT": {"log_file"}}

    def test_managed_options_missing_file(self, tmp_path):
        """A missing rendered file means nothing is managed."""
        assert validator.managed_options(str(tmp_path / "absent.conf")) == {}


class TestSchemaCheck:
    """Tests for the option name and value checks."""

    @pytest.fixture(autouse=True)
    def _oslo(self):
        """Skip when oslo.config is not available."""
        pytest.importorskip("oslo_config")

    def test_unknown_and_invalid_options(self, tmp_path, monkeypatch):
        """Unknown names and values of the wrong type are reported."""
        from oslo_config import (
            cfg,
        )

        options = {
            "api": {
                "workers": cfg.IntOpt("workers"),
                "debug": cfg.BoolOpt("debug"),
            }
        }
        # check_schema registers the options it gets handed, so hand it the
        # real oslo option objects for a service-like namespace.
        monkeypatch.setattr(
            validator,
            "load_registered_options",
            lambda namespaces: (options, []),
        )
        candidate_file = tmp_path / "candidate.conf"
        candidate_file.write_text(
            "[api]\nworkers = not-a-number\nunknown_thing = 1\n"
        )
        candidate, errors, _ = validator.parse_ini(candidate_file.read_text())
        assert errors == []

        result = validator.check_schema(
            candidate, str(candidate_file), ["fake"], [], False
        )

        assert result["status"] == validator.CHECK_FAILED
        assert result["unknown_options"] == ["api/unknown_thing"]
        assert len(result["invalid_values"]) == 1
        assert "api/workers" in result["invalid_values"][0]

    def test_unknown_options_can_be_warnings(self, tmp_path, monkeypatch):
        """--unknown-is-warning downgrades unknown option names."""
        from oslo_config import (
            cfg,
        )

        options = {"api": {"workers": cfg.IntOpt("workers")}}
        monkeypatch.setattr(
            validator,
            "load_registered_options",
            lambda namespaces: (options, []),
        )
        candidate_file = tmp_path / "candidate.conf"
        candidate_file.write_text("[api]\nunknown_thing = 1\n")
        candidate, _, _ = validator.parse_ini(candidate_file.read_text())

        result = validator.check_schema(
            candidate, str(candidate_file), ["fake"], [], True
        )

        assert result["status"] == validator.CHECK_OK
        assert result["unknown_options"] == ["api/unknown_thing"]

    def test_multi_str_opt_suppresses_duplicate_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """Repeating a MultiStrOpt does not emit a duplicate option warning."""
        from oslo_config import (
            cfg,
        )

        options = {
            "pci": {
                "alias": cfg.MultiStrOpt("alias"),
                "workers": cfg.IntOpt("workers"),
            }
        }
        monkeypatch.setattr(
            validator,
            "load_registered_options",
            lambda namespaces: (options, []),
        )
        candidate_file = tmp_path / "candidate.conf"
        candidate_file.write_text(
            "[pci]\nalias = a1\nalias = a2\nworkers = 1\nworkers = 2\n"
        )

        assert validator.main(["--config", str(candidate_file)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["valid"] is True
        assert out["warnings"] == [
            "duplicate option pci/workers (last value wins)"
        ]


class TestMain:
    """Tests for the command line interface."""

    def test_nothing_to_validate(self, capsys):
        """Running without a candidate is an error."""
        assert validator.main([]) == 1
        assert json.loads(capsys.readouterr().out) == {
            "valid": False,
            "errors": ["nothing to validate"],
        }

    def test_valid_file_without_service_metadata(self, tmp_path, capsys):
        """Checks that do not need the service metadata still run."""
        candidate = tmp_path / "candidate.conf"
        candidate.write_text("[api]\nworkers = 3\n")
        main_conf = tmp_path / "glance-api.conf"
        main_conf.write_text("[DEFAULT]\nlog_file = /var/log/glance/api.log\n")

        assert (
            validator.main(
                [
                    "--config",
                    str(candidate),
                    "--main-conf",
                    str(main_conf),
                ]
            )
            == 0
        )
        result = json.loads(capsys.readouterr().out)
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["checks"]["syntax"] == validator.CHECK_OK
        assert result["checks"]["managed_keys"] == validator.CHECK_OK

    def test_managed_option_conflict(self, tmp_path, capsys):
        """Overriding a charm rendered option fails."""
        candidate = tmp_path / "candidate.conf"
        candidate.write_text("[DEFAULT]\nlog_file = /tmp/other.log\n")
        main_conf = tmp_path / "glance-api.conf"
        main_conf.write_text("[DEFAULT]\nlog_file = /var/log/glance/api.log\n")

        assert (
            validator.main(
                [
                    "--config",
                    str(candidate),
                    "--main-conf",
                    str(main_conf),
                ]
            )
            == 1
        )
        result = json.loads(capsys.readouterr().out)
        assert result["valid"] is False
        assert result["managed_conflicts"] == ["DEFAULT/log_file"]
        assert result["checks"]["managed_keys"] == validator.CHECK_FAILED

    def test_blocked_option(self, tmp_path, capsys):
        """Blocked options fail validation."""
        candidate = tmp_path / "candidate.conf"
        candidate.write_text("[database]\nconnection = sqlite://\n")

        assert (
            validator.main(
                [
                    "--config",
                    str(candidate),
                    "--blocklist",
                    json.dumps(["database/connection"]),
                ]
            )
            == 1
        )
        result = json.loads(capsys.readouterr().out)
        assert result["valid"] is False
        assert result["blocklist_hits"] == ["database/connection"]

    def test_overridable_exception_passes(self, tmp_path, capsys):
        """An overridable rule allows the managed option."""
        candidate = tmp_path / "candidate.conf"
        candidate.write_text("[DEFAULT]\nlog_file = /tmp/other.log\n")
        main_conf = tmp_path / "glance-api.conf"
        main_conf.write_text("[DEFAULT]\nlog_file = /var/log/glance/api.log\n")

        assert (
            validator.main(
                [
                    "--config",
                    str(candidate),
                    "--main-conf",
                    str(main_conf),
                    "--overridable",
                    json.dumps(["DEFAULT/log_file"]),
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out)["valid"] is True


def install_fake_stevedore(monkeypatch, namespaces):
    """Install a faithful ``stevedore.named`` double.

    ``plugin`` is the loaded entry point callable and ``obj`` holds its
    result only when the manager is asked to invoke the entry point on
    load, exactly like stevedore behaves.
    """

    class _Extension:
        def __init__(self, name, plugin, invoked):
            self.name = name
            self.plugin = plugin
            self.obj = plugin() if invoked else None

    class _Manager(list):
        def __init__(
            self, namespace, names=None, invoke_on_load=False, **kwargs
        ):
            super().__init__(
                _Extension(name, namespaces[name], invoke_on_load)
                for name in names or []
            )

    named = types.ModuleType("stevedore.named")
    named.NamedExtensionManager = _Manager
    package = types.ModuleType("stevedore")
    package.named = named
    # oslo.policy loads its check extensions through
    # ``stevedore.ExtensionManager`` while parsing rules; the double yields
    # no extension checks so parsing falls back to the built-in checks.
    package.ExtensionManager = _Manager
    monkeypatch.setitem(sys.modules, "stevedore", package)
    monkeypatch.setitem(sys.modules, "stevedore.named", named)


class TestNamespaceLoading:
    """The oslo.config.opts and oslo.policy.policies call convention."""

    def test_options_are_collected_through_the_plugin(self, monkeypatch):
        """Namespaces are called through ``plugin``, as oslo does."""
        install_fake_stevedore(
            monkeypatch,
            {
                "glance": lambda: [
                    (None, [types.SimpleNamespace(name="workers")])
                ]
            },
        )

        options, errors = validator.load_registered_options(["glance"])

        assert errors == []
        assert options["DEFAULT"]["workers"].name == "workers"

    def test_policy_rules_are_collected_through_the_plugin(
        self, monkeypatch, tmp_path
    ):
        """Policy rule names come from the namespace plugin."""
        install_fake_stevedore(
            monkeypatch,
            {"glance": lambda: [types.SimpleNamespace(name="get_images")]},
        )
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("get_images: role:admin\n")

        policy = validator.check_policy(str(policy_file), "glance")

        assert [w for w in policy["warnings"] if "namespace" in w] == []


class TestServiceRuleSyntax:
    """Rules written in a service's own policy language."""

    def test_neutron_rule_names_resolve(self, monkeypatch, tmp_path):
        """Neutron rule names (down to attribute specific rules) resolve."""
        install_fake_stevedore(
            monkeypatch,
            {
                "neutron": lambda: [
                    types.SimpleNamespace(name="create_subnet"),
                    types.SimpleNamespace(name="create_port:fixed_ips"),
                ]
            },
        )
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            'create_subnet: "role:admin"\n'
            "create_port:fixed_ips: field:networks:shared=True or "
            "project_id:%(network:project_id)s\n"
        )

        policy = validator.check_policy(str(policy_file), "neutron")

        assert policy["unknown_rules"] == []
        assert policy["status"] == validator.CHECK_OK

    def test_custom_alias_rule_referenced_by_known_rule_passes(
        self, monkeypatch, tmp_path
    ):
        """A custom rule alias referenced via rule:<alias> is accepted."""
        install_fake_stevedore(
            monkeypatch,
            {
                "neutron": lambda: [
                    types.SimpleNamespace(name="create_subnet"),
                ]
            },
        )
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            'is_subnet_admin: "role:admin"\n'
            'create_subnet: "rule:is_subnet_admin"\n'
        )

        policy = validator.check_policy(str(policy_file), "neutron")

        assert policy["unknown_rules"] == []
        assert policy["status"] == validator.CHECK_OK

    def test_neutron_check_kinds_parse_with_oslo(self, tmp_path):
        """Neutron's field:/project_id: forms are valid oslo policy syntax.

        Runs wherever ``oslo.policy`` is installed, which is the case in the
        workload container the validator runs in; the bare ops-sunbeam test
        environment deliberately does not install service libraries.
        """
        pytest.importorskip("oslo_policy")
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "create_port:fixed_ips: field:networks:shared=True or "
            "project_id:%(network:project_id)s\n"
        )

        policy = validator.check_policy(str(policy_file), None)

        assert policy["syntax_errors"] == []
        assert policy["rules"] == 1
