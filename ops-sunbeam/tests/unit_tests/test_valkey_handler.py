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

"""Test ops_sunbeam.relation_handlers.ValkeyRequiresHandler."""

import configparser
import pathlib
from unittest.mock import (
    patch,
)

import jinja2
import ops
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import ops_sunbeam.test_utils as test_utils

from . import (
    test_charms,
)

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parents[3] / "templates"

CHARM_METADATA = test_charms.CHARM_METADATA + """
requires:
  distributed-cache:
    interface: valkey_client
    optional: true
    limit: 1
"""

SENTINELS = (
    "valkey-0.valkey-endpoints.m.svc.cluster.local:26379,"
    "valkey-1.valkey-endpoints.m.svc.cluster.local:26379,"
    "valkey-2.valkey-endpoints.m.svc.cluster.local:26379"
)
PRIMARY = "valkey-primary.m.svc.cluster.local:6379"
CA = "-----BEGIN CERTIFICATE-----\nMIIfake\n-----END CERTIFICATE-----"


def render(template: str, context: dict) -> str:
    """Render a shared template with the given context."""
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR))
    )
    return env.get_template(template).render(context)


def parse_cache_section(rendered: str) -> dict:
    """Parse the rendered [cache] section into a dict."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(rendered)
    return dict(parser["cache"])


class _ValkeyHandlerTestCase(test_utils.CharmTestCase):
    """Common setup for distributed-cache (valkey_client) tests."""

    PATCHES = []

    def setUp(self) -> None:
        """Charm test class setup."""
        super().setUp(sunbeam_rhandlers, self.PATCHES)
        self.harness = test_utils.get_harness(
            test_charms.MyCharm,
            CHARM_METADATA,
            test_utils.ContainerCalls(),
            charm_config=test_charms.CHARM_CONFIG,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.set_leader()
        self.harness.begin()

    @property
    def handler(self) -> sunbeam_rhandlers.ValkeyRequiresHandler:
        """The distributed-cache handler of the charm."""
        return self.harness.charm.distributed_cache

    def add_relation(self) -> int:
        """Add the distributed-cache relation without provider data."""
        rel_id = self.harness.add_relation("distributed-cache", "valkey")
        self.harness.add_relation_unit(rel_id, "valkey/0")
        return rel_id

    def add_user_secret(
        self, username: str = "relation-5-abc", password: str = "s3cret"
    ) -> str:
        """Add a provider owned user secret granted to the charm."""
        secret_id = self.harness.add_model_secret(
            "valkey", {"username": username, "password": password}
        )
        self.harness.grant_secret(secret_id, self.harness.charm.app.name)
        return secret_id

    def provide(self, rel_id: int, **extra: str) -> None:
        """Publish provider data, as the Valkey operator does."""
        data = {
            "secret-user": self.add_user_secret(),
            "endpoints": PRIMARY,
            "read-only-endpoints": "valkey-1.m:6379,valkey-2.m:6379",
            "sentinel-endpoints": SENTINELS,
            "mode": "sentinel",
            "tls": "False",
            "tls-ca": "None",
            "version": "9.0.4",
        }
        data.update(extra)
        self.harness.update_relation_data(rel_id, "valkey", data)


class TestValkeyRequiresHandler(_ValkeyHandlerTestCase):
    """Tests for the distributed-cache (valkey_client) handler."""

    def test_handler_registered_from_metadata(self) -> None:
        """The base charm adds the handler when the relation exists."""
        self.assertIsInstance(
            self.handler, sunbeam_rhandlers.ValkeyRequiresHandler
        )
        self.assertIn(self.handler, self.harness.charm.relation_handlers)
        self.assertFalse(self.handler.mandatory)
        self.assertEqual(
            self.handler.ca_file_path,
            "/etc/my-service/distributed-cache-ca.pem",
        )

    def test_handler_not_registered_without_metadata(self) -> None:
        """Charms without the relation get no handler."""
        harness = test_utils.get_harness(
            test_charms.MyCharm,
            test_charms.CHARM_METADATA,
            test_utils.ContainerCalls(),
            charm_config=test_charms.CHARM_CONFIG,
            initial_charm_config=test_charms.INITIAL_CHARM_CONFIG,
        )
        self.addCleanup(harness.cleanup)
        harness.begin()
        self.assertFalse(hasattr(harness.charm, "distributed_cache"))

    def test_requests_resource(self) -> None:
        """The leader requests the app key prefix and secret fields."""
        rel_id = self.add_relation()
        rel_data = self.harness.get_relation_data(rel_id, "my-service")
        self.assertEqual(rel_data["database"], "my-service:*")
        self.assertIn("requested-secrets", rel_data)

    def test_not_ready_without_relation(self) -> None:
        """No relation: not ready, empty context, no cache rendered."""
        self.assertFalse(self.handler.ready)
        self.assertEqual(self.handler.context(), {})
        rendered = render("parts/section-oslo-cache", {})
        self.assertEqual(parse_cache_section(rendered), {})

    def test_not_ready_without_credentials(self) -> None:
        """Endpoints without the user secret are not enough."""
        rel_id = self.add_relation()
        self.harness.update_relation_data(
            rel_id, "valkey", {"endpoints": PRIMARY}
        )
        self.assertFalse(self.handler.ready)
        self.assertEqual(self.handler.context(), {})

    def test_context_sentinel(self) -> None:
        """Sentinel endpoints with client TLS select redis_sentinel."""
        self.provide(self.add_relation(), tls="True", **{"tls-ca": CA})
        self.assertTrue(self.handler.ready)
        self.assertEqual(
            self.handler.context(),
            {
                "username": "relation-5-abc",
                "password": "s3cret",
                "backend": "dogpile.cache.redis_sentinel",
                "redis_sentinels": SENTINELS,
                "redis_sentinel_service_name": "primary",
                "tls_enabled": True,
                "tls_ca": CA,
                "tls_cafile": "/etc/my-service/distributed-cache-ca.pem",
            },
        )

    def test_context_direct_when_tls_disabled(self) -> None:
        """Without client TLS, Sentinel is not used (valkey-operator#230).

        Sentinel always announces the TLS port, so the handler connects
        to the primary Service even though sentinel endpoints exist.
        """
        self.provide(self.add_relation())
        self.assertEqual(
            self.handler.context(),
            {
                "username": "relation-5-abc",
                "password": "s3cret",
                "backend": "dogpile.cache.redis",
                "redis_server": PRIMARY,
                "tls_enabled": False,
            },
        )

    def test_context_direct_without_sentinel_endpoints(self) -> None:
        """No sentinel endpoints published: fall back to direct."""
        self.provide(
            self.add_relation(),
            tls="True",
            **{"tls-ca": CA, "sentinel-endpoints": ""},
        )
        ctxt = self.handler.context()
        self.assertEqual(ctxt["backend"], "dogpile.cache.redis")
        self.assertEqual(ctxt["redis_server"], PRIMARY)

    def test_context_tls_from_databag(self) -> None:
        """TLS data published in the databag."""
        self.provide(self.add_relation(), tls="True", **{"tls-ca": CA})
        ctxt = self.handler.context()
        self.assertTrue(ctxt["tls_enabled"])
        self.assertEqual(ctxt["tls_ca"], CA)
        self.assertEqual(
            ctxt["tls_cafile"], "/etc/my-service/distributed-cache-ca.pem"
        )

    def test_context_tls_from_secret(self) -> None:
        """TLS data published in the secret-tls secret."""
        rel_id = self.add_relation()
        tls_secret = self.harness.add_model_secret(
            "valkey", {"tls": "True", "tls-ca": CA}
        )
        self.harness.grant_secret(tls_secret, self.harness.charm.app.name)
        self.provide(rel_id, **{"secret-tls": tls_secret})
        # Mirror the v0 secrets layout: no plaintext tls fields.
        self.harness.update_relation_data(
            rel_id, "valkey", {"tls": "", "tls-ca": ""}
        )
        ctxt = self.handler.context()
        self.assertTrue(ctxt["tls_enabled"])
        self.assertEqual(ctxt["tls_ca"], CA)

    def test_context_tls_without_ca(self) -> None:
        """TLS on without a CA: rely on the system trust store."""
        self.provide(self.add_relation(), tls="True")
        ctxt = self.handler.context()
        self.assertTrue(ctxt["tls_enabled"])
        self.assertNotIn("tls_ca", ctxt)
        self.assertNotIn("tls_cafile", ctxt)

    def test_context_secret_not_accessible(self) -> None:
        """An unreadable secret yields no context rather than an error."""
        self.provide(self.add_relation())
        self.assertTrue(self.handler.ready)
        with patch.object(
            self.handler.model,
            "get_secret",
            side_effect=ops.SecretNotFoundError("gone"),
        ):
            self.assertEqual(self.handler.context(), {})

    def test_context_picks_up_rotated_password(self) -> None:
        """The latest secret revision is used."""
        rel_id = self.add_relation()
        secret_id = self.add_user_secret()
        self.provide(rel_id, **{"secret-user": secret_id})
        self.harness.set_secret_content(
            secret_id, {"username": "relation-5-abc", "password": "n3w"}
        )
        self.assertEqual(self.handler.context()["password"], "n3w")

    def test_relation_changed_triggers_configure(self) -> None:
        """Provider data reaching the charm triggers configure_charm."""
        rel_id = self.add_relation()
        self.harness.charm.seen_events = []
        self.provide(rel_id)
        self.assertIn("RelationChangedEvent", self.harness.charm.seen_events)

    def test_relation_changed_not_ready_skips_configure(self) -> None:
        """Incomplete data does not trigger configure_charm."""
        rel_id = self.add_relation()
        self.harness.charm.seen_events = []
        self.harness.update_relation_data(
            rel_id, "valkey", {"endpoints": PRIMARY}
        )
        self.assertNotIn(
            "RelationChangedEvent", self.harness.charm.seen_events
        )

    def test_relation_broken_triggers_configure(self) -> None:
        """Removing the relation re-renders without the cache."""
        rel_id = self.add_relation()
        self.provide(rel_id)
        self.harness.charm.seen_events = []
        self.harness.remove_relation(rel_id)
        self.assertIn("RelationBrokenEvent", self.harness.charm.seen_events)

    def test_contexts_namespaced_under_relation(self) -> None:
        """Charm contexts expose the data as distributed_cache."""
        self.provide(self.add_relation())
        contexts = self.harness.charm.contexts()
        self.assertEqual(
            contexts.distributed_cache.backend, "dogpile.cache.redis"
        )


class TestOsloCacheTemplate(_ValkeyHandlerTestCase):
    """Render templates/parts/section-oslo-cache from handler contexts."""

    def render_cache(self) -> dict:
        """Render the cache section from the handler context."""
        rendered = render(
            "parts/section-oslo-cache",
            {"distributed_cache": self.handler.context()},
        )
        return parse_cache_section(rendered)

    def test_render_sentinel(self) -> None:
        """Sentinel backend options (client TLS on)."""
        self.provide(self.add_relation(), tls="True", **{"tls-ca": CA})
        self.assertEqual(
            self.render_cache(),
            {
                "enabled": "true",
                "backend": "dogpile.cache.redis_sentinel",
                "redis_sentinels": SENTINELS,
                "redis_sentinel_service_name": "primary",
                "username": "relation-5-abc",
                "password": "s3cret",
                "tls_enabled": "true",
                "tls_cafile": "/etc/my-service/distributed-cache-ca.pem",
            },
        )

    def test_render_direct(self) -> None:
        """Direct backend options (client TLS off)."""
        self.provide(self.add_relation())
        self.assertEqual(
            self.render_cache(),
            {
                "enabled": "true",
                "backend": "dogpile.cache.redis",
                "redis_server": PRIMARY,
                "username": "relation-5-abc",
                "password": "s3cret",
            },
        )

    def test_render_tls(self) -> None:
        """TLS options."""
        self.provide(self.add_relation(), tls="True", **{"tls-ca": CA})
        cache = self.render_cache()
        self.assertEqual(cache["tls_enabled"], "true")
        self.assertEqual(
            cache["tls_cafile"], "/etc/my-service/distributed-cache-ca.pem"
        )
        rendered_ca = render(
            "distributed-cache-ca.pem.j2",
            {"distributed_cache": self.handler.context()},
        )
        self.assertEqual(rendered_ca.strip(), CA)

    def test_render_ca_empty_without_tls(self) -> None:
        """The CA file is empty when TLS is off."""
        self.provide(self.add_relation())
        rendered_ca = render(
            "distributed-cache-ca.pem.j2",
            {"distributed_cache": self.handler.context()},
        )
        self.assertEqual(rendered_ca.strip(), "")

    def test_render_memcache_fallback(self) -> None:
        """The existing memcache branch still works without the relation."""
        rendered = render(
            "parts/section-oslo-cache", {"memcache_url": "10.0.0.1:11211"}
        )
        self.assertEqual(
            parse_cache_section(rendered),
            {
                "enabled": "true",
                "backend": "oslo_cache.memcache_pool",
                "memcache_servers": "10.0.0.1:11211",
            },
        )
