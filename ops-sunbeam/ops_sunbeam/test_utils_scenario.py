# Copyright 2025 Canonical Ltd.
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

"""Reusable factories and helpers for ops.testing (state-transition) tests."""

from __future__ import (
    annotations,
)

import dataclasses
import json
import pathlib
import random
from typing import (
    Callable,
)

from ops import (
    testing,
)

# ---------------------------------------------------------------------------
# Exec factories
# ---------------------------------------------------------------------------


def a2ensite_exec(site: str = "wsgi-*") -> testing.Exec:
    """Mock a2ensite command."""
    return testing.Exec(command_prefix=["a2ensite", site], return_code=0)


def a2dissite_exec(site: str = "000-default") -> testing.Exec:
    """Mock a2dissite command."""
    return testing.Exec(command_prefix=["a2dissite", site], return_code=0)


def a2enmod_exec(mod: str = "wsgi") -> testing.Exec:
    """Mock a2enmod command."""
    return testing.Exec(command_prefix=["a2enmod", mod], return_code=0)


def db_sync_exec(
    service: str = "placement", cmd: str = "db sync"
) -> testing.Exec:
    """Mock 'sudo -u <service> <service>-manage db sync' style commands."""
    return testing.Exec(command_prefix=["sudo"], return_code=0)


def sudo_exec() -> testing.Exec:
    """Catch-all for sudo commands (db-sync, cell_create, etc.)."""
    return testing.Exec(command_prefix=["sudo"], return_code=0)


# ---------------------------------------------------------------------------
# Container helpers
# ---------------------------------------------------------------------------


def k8s_container(
    name: str,
    can_connect: bool = True,
    tmp_path: pathlib.Path | None = None,
    execs: list[testing.Exec] | None = None,
) -> testing.Container:
    """Create a container with optional filesystem mount and exec mocks."""
    mounts = {}
    if tmp_path:
        mount_dir = tmp_path / name
        mount_dir.mkdir(parents=True, exist_ok=True)
        mounts["root"] = testing.Mount(location="/", source=mount_dir)
    return testing.Container(  # type: ignore[call-arg]
        name=name,
        can_connect=can_connect,
        mounts=mounts,
        execs=frozenset(execs or []),
    )


def k8s_api_container(
    name: str,
    wsgi_site: str | None = None,
    can_connect: bool = True,
    tmp_path: pathlib.Path | None = None,
    extra_execs: list[testing.Exec] | None = None,
) -> testing.Container:
    """Create a container for a K8s API service with standard exec mocks.

    Includes a2dissite, a2ensite, and sudo (db-sync) exec mocks.
    """
    execs: list[testing.Exec] = [
        testing.Exec(command_prefix=["a2dissite"], return_code=0),
        testing.Exec(command_prefix=["a2ensite"], return_code=0),
        testing.Exec(command_prefix=["sudo"], return_code=0),
    ]
    if extra_execs:
        execs.extend(extra_execs)
    return k8s_container(name, can_connect, tmp_path, execs)


def containers_from_metadata(
    metadata: dict,
    can_connect: bool = True,
    tmp_path: pathlib.Path | None = None,
    container_execs: dict[str, list[testing.Exec]] | None = None,
) -> list[testing.Container]:
    """Create all containers declared in metadata with optional per-container exec mocks."""
    _container_execs = container_execs or {}
    return [
        k8s_container(
            name=name,
            can_connect=can_connect,
            tmp_path=tmp_path,
            execs=_container_execs.get(name, []),
        )
        for name in metadata.get("containers", {})
    ]


def mandatory_relations_from_charmcraft(
    charm_root: pathlib.Path,
) -> frozenset[str]:
    """Derive mandatory relation names from charmcraft.yaml.

    Returns the set of requires-relation endpoints that do **not** carry
    ``optional: true``.  This is the single source of truth used by the
    charm runtime (``OSBaseOperatorCharm.__init__``), so tests should use
    it too instead of maintaining a hand-written constant.
    """
    import yaml

    charmcraft_path = charm_root / "charmcraft.yaml"
    with open(charmcraft_path) as fh:
        data = yaml.safe_load(fh)
    return frozenset(
        name
        for name, meta in data.get("requires", {}).items()
        if isinstance(meta, dict) and not meta.get("optional", False)
    )


# ---------------------------------------------------------------------------
# DatabaseRequiresEvents cleanup
# ---------------------------------------------------------------------------


def cleanup_database_requires_events():
    """Remove dynamically-defined events to allow fresh Context creation.

    Usage in conftest.py::

        @pytest.fixture(autouse=True)
        def _cleanup_db_events():
            yield
            cleanup_database_requires_events()
    """
    from charms.data_platform_libs.v0.data_interfaces import (
        DatabaseRequiresEvents,
    )

    for attr in list(vars(DatabaseRequiresEvents)):
        if attr.endswith(
            (
                "_database_created",
                "_endpoints_changed",
                "_read_only_endpoints_changed",
            )
        ):
            try:
                delattr(DatabaseRequiresEvents, attr)
            except AttributeError:
                pass


# =========================================================================
# Database (mysql_client) — DBHandler
# =========================================================================


def db_credentials_secret(
    secret_id: str = "secret:db-creds",
) -> testing.Secret:
    """Secret containing database credentials (owned by remote mysql app)."""
    return testing.Secret(
        tracked_content={"username": "foo", "password": "hardpassword"},
        id=secret_id,
        owner=None,
    )


def db_relation_empty(
    endpoint: str = "database",
) -> testing.Relation:
    """Database relation with no remote app data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="mysql",
        remote_app_data={},
        remote_units_data={0: {"ingress-address": "10.0.0.3"}},
    )


def db_relation_complete(
    endpoint: str = "database",
    secret_id: str = "secret:db-creds",
) -> testing.Relation:
    """Database relation with endpoints and secret-user set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="mysql",
        remote_app_data={
            "secret-user": secret_id,
            "endpoints": "10.0.0.10",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.3"}},
    )


# =========================================================================
# AMQP / RabbitMQ — RabbitMQHandler / AMQPHandler
# =========================================================================


def amqp_relation_empty(
    endpoint: str = "amqp",
) -> testing.Relation:
    """AMQP relation with no remote app data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="rabbitmq",
        remote_app_data={},
        remote_units_data={0: {"ingress-address": "10.0.0.13"}},
    )


def amqp_relation_complete(
    endpoint: str = "amqp",
) -> testing.Relation:
    """AMQP relation with hostname and password set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="rabbitmq",
        remote_app_data={
            "hostname": "rabbithost1.local",
            "password": "rabbit.pass",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.13"}},
    )


# =========================================================================
# Identity Service (keystone) — IdentityServiceRequiresHandler
# =========================================================================


def identity_service_secret(
    secret_id: str = "secret:id-svc-creds",
) -> testing.Secret:
    """Secret containing identity-service credentials (owned by keystone)."""
    return testing.Secret(
        tracked_content={"username": "svcuser1", "password": "svcpass1"},
        id=secret_id,
        owner=None,
    )


def identity_service_relation_empty(
    endpoint: str = "identity-service",
) -> testing.Relation:
    """Identity-service relation with no remote app data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={},
        remote_units_data={0: {"ingress-address": "10.0.0.33"}},
    )


def identity_service_relation_complete(
    endpoint: str = "identity-service",
    secret_id: str = "secret:id-svc-creds",
) -> testing.Relation:
    """Identity-service relation with all fields set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={
            "admin-domain-id": "admindomid1",
            "admin-project-id": "adminprojid1",
            "admin-user-id": "adminuserid1",
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "service-domain": "servicedom",
            "service-domain_id": "svcdomid1",
            "service-domain-name": "svc-domain",
            "service-host": "keystone.service",
            "service-port": "5000",
            "service-protocol": "http",
            "service-project": "svcproj1",
            "service-project-name": "svc-project",
            "service-project-id": "svcprojid1",
            "service-credentials": secret_id,
            "region": "region12",
            "project-domain-name": "svc-domain",
            "user-domain-name": "svc-domain",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.33"}},
    )


# =========================================================================
# Identity Credentials — IdentityCredentialsRequiresHandler
# =========================================================================


def identity_credentials_secret(
    secret_id: str = "secret:id-creds",
) -> testing.Secret:
    """Secret containing identity credentials (owned by keystone)."""
    return testing.Secret(
        tracked_content={"username": "username", "password": "user-password"},
        id=secret_id,
        owner=None,
    )


def identity_credentials_relation_empty(
    endpoint: str = "identity-credentials",
) -> testing.Relation:
    """Identity-credentials relation with no remote app data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={},
        remote_units_data={0: {"ingress-address": "10.0.0.35"}},
    )


def identity_credentials_relation_complete(
    endpoint: str = "identity-credentials",
    secret_id: str = "secret:id-creds",
) -> testing.Relation:
    """Identity-credentials relation with all fields set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="keystone",
        remote_app_data={
            "api-version": "3",
            "auth-host": "keystone.local",
            "auth-port": "12345",
            "auth-protocol": "http",
            "internal-host": "keystone.internal",
            "internal-port": "5000",
            "internal-protocol": "http",
            "credentials": secret_id,
            "project-name": "user-project",
            "project-id": "uproj-id",
            "user-domain-name": "udomain-name",
            "user-domain-id": "udomain-id",
            "project-domain-name": "pdomain_-ame",
            "project-domain-id": "pdomain-id",
            "region": "region12",
            "public-endpoint": "http://10.20.21.11:80/openstack-keystone",
            "internal-endpoint": "http://10.153.2.45:80/openstack-keystone",
        },
        remote_units_data={0: {"ingress-address": "10.0.0.35"}},
    )


# =========================================================================
# Ingress (internal + public) — IngressHandler
# =========================================================================


def ingress_relation_empty(
    endpoint: str = "ingress-internal",
) -> testing.Relation:
    """Ingress relation with no remote app data."""
    endpoint_type = endpoint.split("-", 1)[1] if "-" in endpoint else endpoint
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name=f"traefik-{endpoint_type}",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def ingress_relation_complete(
    endpoint: str = "ingress-internal",
    url: str | None = None,
) -> testing.Relation:
    """Ingress relation with url data set."""
    endpoint_type = endpoint.split("-", 1)[1] if "-" in endpoint else endpoint
    if url is None:
        url = f"http://{endpoint_type}-url"
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name=f"traefik-{endpoint_type}",
        remote_app_data={"ingress": json.dumps({"url": url})},
        remote_units_data={0: {}},
    )


def ingress_internal_relation_empty() -> testing.Relation:
    """Ingress-internal relation with no remote app data."""
    return ingress_relation_empty(endpoint="ingress-internal")


def ingress_internal_relation_complete(
    url: str = "http://internal-url",
) -> testing.Relation:
    """Ingress-internal relation with url data set."""
    return ingress_relation_complete(endpoint="ingress-internal", url=url)


def ingress_public_relation_empty() -> testing.Relation:
    """Ingress-public relation with no remote app data."""
    return ingress_relation_empty(endpoint="ingress-public")


def ingress_public_relation_complete(
    url: str = "http://public-url",
) -> testing.Relation:
    """Ingress-public relation with url data set."""
    return ingress_relation_complete(endpoint="ingress-public", url=url)


# =========================================================================
# Peers — BasePeerHandler
# =========================================================================


def peer_relation(
    endpoint: str = "peers",
) -> testing.PeerRelation:
    """Peer relation."""
    return testing.PeerRelation(endpoint=endpoint)


# =========================================================================
# Certificates (tls-certificates) — TlsCertificatesHandler
# =========================================================================


def certificates_relation_empty(
    endpoint: str = "certificates",
) -> testing.Relation:
    """Certificates relation with no remote app data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="vault",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def certificates_relation_complete(
    endpoint: str = "certificates",
) -> testing.Relation:
    """Certificates relation with certificate data present."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="vault",
        remote_app_data={"certificates": "TEST_CERT_LIST"},
        remote_units_data={0: {}},
    )


# =========================================================================
# Ceph — CephClientHandler
# =========================================================================


def ceph_relation_empty(
    endpoint: str = "ceph",
) -> testing.Relation:
    """Ceph relation with no credentials data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ceph-mon",
        remote_app_data={},
        remote_units_data={0: {"ingress-address": "10.0.0.33"}},
    )


def ceph_relation_complete(
    endpoint: str = "ceph",
    request_id: str = "req-1",
    client_unit: str = "myapp-0",
) -> testing.Relation:
    """Ceph relation with auth, key, and broker response set.

    The broker response uses a fixed request-id; callers whose charm
    generates a real broker_req should override *request_id* to match.
    """
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ceph-mon",
        remote_app_data={},
        remote_units_data={
            0: {
                "auth": "cephx",
                "key": "AQBUfpVeNl7CHxAA8/f6WTcYFxW2dJ5VyvWmJg==",
                "ingress-address": "192.0.2.2",
                "ceph-public-address": "192.0.2.2",
                f"broker-rsp-{client_unit}": json.dumps(
                    {"exit-code": 0, "request-id": request_id}
                ),
            },
            1: {},
        },
    )


# =========================================================================
# Logging / Loki — LogForwardHandler
# =========================================================================


def logging_relation_empty(
    endpoint: str = "logging",
) -> testing.Relation:
    """Logging relation with no endpoint data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="loki",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def logging_relation_complete(
    endpoint: str = "logging",
    url: str = "http://10.20.23.1/cos-loki-0/loki/api/v1/push",
) -> testing.Relation:
    """Logging relation with endpoint url set (in unit data)."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="loki",
        remote_app_data={},
        remote_units_data={
            0: {"endpoint": json.dumps({"url": url})},
        },
    )


# =========================================================================
# Tracing — TracingRequireHandler
# =========================================================================


def tracing_relation_empty(
    endpoint: str = "tracing",
) -> testing.Relation:
    """Tracing relation with no provider data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="tempo",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def tracing_relation_complete(
    endpoint: str = "tracing",
    otlp_http_url: str = "http://tempo:4318/v1/traces",
) -> testing.Relation:
    """Tracing relation with receivers published by the provider."""
    receivers = [
        {
            "protocol": {"name": "otlp_http", "type": "http"},
            "url": otlp_http_url,
        },
    ]
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="tempo",
        remote_app_data={"receivers": json.dumps(receivers)},
        remote_units_data={0: {}},
    )


# =========================================================================
# Certificate Transfer — CertificateTransferRequiresHandler
# =========================================================================

_DUMMY_CA = (
    "-----BEGIN CERTIFICATE-----\nMIIDummyCA\n-----END CERTIFICATE-----"
)
_DUMMY_CHAIN = [
    "-----BEGIN CERTIFICATE-----\nMIIDummyChain\n-----END CERTIFICATE-----",
]


def certificate_transfer_relation_empty(
    endpoint: str = "receive-ca-cert",
) -> testing.Relation:
    """Certificate-transfer relation with no data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ca-provider",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def certificate_transfer_relation_complete(
    endpoint: str = "receive-ca-cert",
    ca: str = _DUMMY_CA,
    chain: list[str] | None = None,
) -> testing.Relation:
    """Certificate-transfer relation with ca and chain in remote unit data."""
    if chain is None:
        chain = _DUMMY_CHAIN
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="ca-provider",
        remote_app_data={},
        remote_units_data={
            0: {"ca": ca, "chain": json.dumps(chain)},
        },
    )


# =========================================================================
# Traefik Route — TraefikRouteHandler
# =========================================================================


def traefik_route_relation_empty(
    endpoint: str = "traefik-route",
) -> testing.Relation:
    """Traefik-route relation with no provider data."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="traefik",
        remote_app_data={},
        remote_units_data={0: {}},
    )


def traefik_route_relation_complete(
    endpoint: str = "traefik-route",
    external_host: str = "traefik.external",
    scheme: str = "http",
) -> testing.Relation:
    """Traefik-route relation with external_host and scheme set."""
    return testing.Relation(
        endpoint=endpoint,
        remote_app_name="traefik",
        remote_app_data={
            "external_host": external_host,
            "scheme": scheme,
        },
        remote_units_data={0: {}},
    )


# =========================================================================
# Registry — mapping interface/endpoint names to (empty, complete) factories
# =========================================================================

RELATION_FACTORIES: dict[str, tuple[Callable, Callable]] = {
    "mysql_client": (db_relation_empty, db_relation_complete),
    "rabbitmq": (amqp_relation_empty, amqp_relation_complete),
    "identity-service": (
        identity_service_relation_empty,
        identity_service_relation_complete,
    ),
    "identity-credentials": (
        identity_credentials_relation_empty,
        identity_credentials_relation_complete,
    ),
    "ingress-internal": (
        ingress_internal_relation_empty,
        ingress_internal_relation_complete,
    ),
    "ingress-public": (
        ingress_public_relation_empty,
        ingress_public_relation_complete,
    ),
    "certificates": (
        certificates_relation_empty,
        certificates_relation_complete,
    ),
    "ceph": (ceph_relation_empty, ceph_relation_complete),
    "logging": (logging_relation_empty, logging_relation_complete),
    "tracing": (tracing_relation_empty, tracing_relation_complete),
    "receive-ca-cert": (
        certificate_transfer_relation_empty,
        certificate_transfer_relation_complete,
    ),
    "traefik-route": (
        traefik_route_relation_empty,
        traefik_route_relation_complete,
    ),
}


# =========================================================================
# Assertion helpers
# =========================================================================


def assert_config_file_exists(
    state_out: testing.State,
    ctx: testing.Context,
    container_name: str,
    path: str,
) -> pathlib.Path:
    """Assert a config file exists in the output container filesystem."""
    container = state_out.get_container(container_name)
    fs = container.get_filesystem(ctx)
    file_path = fs / path.lstrip("/")
    assert (
        file_path.exists()
    ), f"Expected file {path} in container {container_name}, not found"
    return file_path


def assert_config_file_contains(
    state_out: testing.State,
    ctx: testing.Context,
    container_name: str,
    path: str,
    expected: list[str],
) -> None:
    """Assert a config file contains expected strings."""
    file_path = assert_config_file_exists(state_out, ctx, container_name, path)
    content = file_path.read_text()
    for exp in expected:
        assert (
            exp in content
        ), f"Expected '{exp}' in {path}, content: {content[:500]}"


def assert_unit_status(
    state_out: testing.State,
    expected_status_name: str,
    message_contains: str | None = None,
) -> None:
    """Assert the unit status name and optionally message."""
    assert (
        state_out.unit_status.name == expected_status_name
    ), f"Expected status '{expected_status_name}', got '{state_out.unit_status}'"
    if message_contains:
        assert message_contains in state_out.unit_status.message, (
            f"Expected '{message_contains}' in status message, "
            f"got '{state_out.unit_status.message}'"
        )


# =========================================================================
# Parametrized test generation helpers
# =========================================================================


def missing_relation_combinations(
    mandatory_relations: set[str],
    all_complete_relations: list,
) -> list[tuple[str, list]]:
    """Generate test cases where each mandatory relation is removed one at a time.

    Returns list of (missing_rel_name, remaining_relations) tuples for use with
    ``@pytest.mark.parametrize``.
    """
    result = []
    for rel_name in sorted(mandatory_relations):
        remaining = [
            r for r in all_complete_relations if r.endpoint != rel_name
        ]
        result.append((rel_name, remaining))
    return result


def incomplete_relation_combinations(
    mandatory_relations: set[str],
    all_complete_relations: list,
    relation_factories: dict[str, tuple[Callable, Callable]],
) -> list[tuple[str, list]]:
    """Generate test cases where each mandatory relation has empty data.

    For each mandatory relation, replace it with its empty variant (from
    *relation_factories*).  Returns list of ``(incomplete_rel_name,
    modified_relations)`` tuples.
    """
    result = []
    for rel_name in sorted(mandatory_relations):
        modified = []
        for rel in all_complete_relations:
            if rel.endpoint == rel_name and rel_name in relation_factories:
                empty_factory = relation_factories[rel_name][0]
                modified.append(empty_factory(rel.endpoint))
            else:
                modified.append(rel)
        result.append((rel_name, modified))
    return result


# =========================================================================
# Shared test functions (importable, called from individual charm tests)
# =========================================================================


def assert_blocked_when_relation_missing(
    ctx: testing.Context,
    base_state: testing.State,
    mandatory_relations: set[str],
    all_complete_relations: list,
) -> None:
    """Verify charm blocks when each mandatory relation is removed.

    Call this from a test method — it runs N sub-assertions (one per mandatory
    relation).
    """
    for rel_name in sorted(mandatory_relations):
        remaining = [
            r for r in all_complete_relations if r.endpoint != rel_name
        ]
        state = dataclasses.replace(base_state, relations=remaining)
        out = ctx.run(ctx.on.config_changed(), state)
        assert out.unit_status.name in (
            "blocked",
            "waiting",
        ), f"Expected blocked/waiting when '{rel_name}' missing, got {out.unit_status}"


def assert_active_when_all_complete(
    ctx: testing.Context,
    complete_state: testing.State,
) -> None:
    """Verify charm reaches active with all relations complete."""
    out = ctx.run(ctx.on.config_changed(), complete_state)
    assert out.unit_status == testing.ActiveStatus(
        ""
    ), f"Expected ActiveStatus, got {out.unit_status}"


def assert_waiting_when_container_not_ready(
    ctx: testing.Context,
    complete_state: testing.State,
) -> None:
    """Verify charm waits when containers can't connect."""
    disconnected = [
        dataclasses.replace(c, can_connect=False)
        for c in complete_state.containers
    ]
    state = dataclasses.replace(complete_state, containers=disconnected)
    out = ctx.run(ctx.on.config_changed(), state)
    assert out.unit_status.name in (
        "blocked",
        "waiting",
    ), f"Expected blocked/waiting with disconnected containers, got {out.unit_status}"


# =========================================================================
# Relation ordering invariance helpers
# =========================================================================


def assert_relation_ordering_invariance(
    ctx: testing.Context,
    base_state: testing.State,
    mandatory_relations: set[str],
    complete_relations: list,
    complete_secrets: list,
    sample_count: int = 10,
    seed: int = 42,
) -> None:
    """Verify charm reaches active regardless of the order relations are added.

    Uses deterministic random sampling to test *sample_count* orderings.
    For each ordering, adds relations one-by-one and verifies:

    - Charm is NOT active until all mandatory relations present
    - Charm IS active (or at least not blocked) once all mandatory relations
      present

    Args:
        ctx: Testing context
        base_state: State with containers etc but NO relations
        mandatory_relations: Set of endpoint names that are mandatory
        complete_relations: List of all complete relation objects
        complete_secrets: List of all secrets needed for complete relations
        sample_count: Number of random orderings to test
        seed: Random seed for reproducibility
    """
    rng = random.Random(seed)
    mandatory_complete = [
        r for r in complete_relations if r.endpoint in mandatory_relations
    ]

    for i in range(sample_count):
        order = list(mandatory_complete)
        rng.shuffle(order)

        for j, rel in enumerate(order):
            partial_rels = order[: j + 1]
            # Add non-mandatory relations too (they shouldn't affect blocking)
            non_mandatory = [
                r
                for r in complete_relations
                if r.endpoint not in mandatory_relations
            ]
            all_rels = partial_rels + non_mandatory

            state = dataclasses.replace(
                base_state,
                relations=all_rels,
                secrets=complete_secrets,
            )
            out = ctx.run(ctx.on.config_changed(), state)

            present_mandatory = {r.endpoint for r in partial_rels}
            if present_mandatory >= mandatory_relations:
                # All mandatory present — should NOT be blocked on missing
                assert (
                    out.unit_status.name != "blocked"
                    or "integration missing" not in out.unit_status.message
                ), (
                    f"Ordering {i}, step {j}: all mandatory present "
                    f"({present_mandatory}) but still blocked: "
                    f"{out.unit_status}"
                )
            else:
                # Some mandatory missing — should be blocked or waiting
                assert out.unit_status.name in ("blocked", "waiting"), (
                    f"Ordering {i}, step {j}: missing "
                    f"{mandatory_relations - present_mandatory} "
                    f"but status is {out.unit_status}"
                )


def assert_each_relation_missing_blocks(
    ctx: testing.Context,
    base_state: testing.State,
    mandatory_relations: set[str],
    complete_relations: list,
    complete_secrets: list,
) -> None:
    """Verify that removing any single mandatory relation causes blocked/waiting.

    This is the O(N) approach: for each mandatory relation, remove it and
    verify the charm is not active.

    Args:
        ctx: Testing context
        base_state: State with containers etc but NO relations
        mandatory_relations: Set of endpoint names that are mandatory
        complete_relations: List of all complete relation objects
        complete_secrets: List of all secrets needed
    """
    for rel_name in sorted(mandatory_relations):
        remaining = [r for r in complete_relations if r.endpoint != rel_name]
        state = dataclasses.replace(
            base_state,
            relations=remaining,
            secrets=complete_secrets,
        )
        out = ctx.run(ctx.on.config_changed(), state)
        assert out.unit_status.name in (
            "blocked",
            "waiting",
        ), f"Expected blocked/waiting when '{rel_name}' missing, got {out.unit_status}"


def assert_container_disconnect_causes_waiting_or_blocked(
    ctx: testing.Context,
    complete_state: testing.State,
):
    """Verify charm goes to waiting/blocked when containers can't connect.

    Replaces all containers with can_connect=False versions.
    """
    disconnected = frozenset(
        dataclasses.replace(c, can_connect=False)
        for c in complete_state.containers
    )
    state = dataclasses.replace(complete_state, containers=disconnected)
    out = ctx.run(ctx.on.config_changed(), state)
    assert out.unit_status.name in (
        "blocked",
        "waiting",
    ), f"Expected blocked/waiting with disconnected containers, got {out.unit_status}"


def assert_leader_vs_non_leader(
    ctx: testing.Context,
    complete_state: testing.State,
):
    """Verify leader gets active, non-leader gets waiting.

    Most sunbeam charms require leader to bootstrap. Non-leader should get
    WaitingStatus("Leader not ready") or similar.
    """
    # Leader should reach active
    leader_state = dataclasses.replace(complete_state, leader=True)
    out = ctx.run(ctx.on.config_changed(), leader_state)
    assert out.unit_status == testing.ActiveStatus(
        ""
    ), f"Leader expected ActiveStatus, got {out.unit_status}"

    # Non-leader should wait
    non_leader_state = dataclasses.replace(complete_state, leader=False)
    out = ctx.run(ctx.on.config_changed(), non_leader_state)
    assert (
        out.unit_status.name == "waiting"
    ), f"Non-leader expected waiting, got {out.unit_status}"


def assert_relation_broken_causes_blocked_or_waiting(
    ctx: testing.Context,
    complete_state: testing.State,
    relation_endpoint: str,
):
    """Verify that breaking a mandatory relation returns charm to blocked/waiting.

    Fires relation_broken for the specified relation endpoint.
    """
    target_rels = [
        r for r in complete_state.relations if r.endpoint == relation_endpoint
    ]
    assert (
        target_rels
    ), f"No relation with endpoint '{relation_endpoint}' in state"
    target_rel = target_rels[0]

    out = ctx.run(ctx.on.relation_broken(target_rel), complete_state)
    assert out.unit_status.name in (
        "blocked",
        "waiting",
    ), f"Expected blocked/waiting after breaking '{relation_endpoint}', got {out.unit_status}"


def assert_pebble_ready_configures_service(
    ctx: testing.Context,
    complete_state: testing.State,
    container_name: str,
    expected_services: list[str] | None = None,
):
    """Verify pebble-ready event configures the expected container services.

    Args:
        ctx: Testing context
        complete_state: Full state with all relations and secrets
        container_name: Name of the container to test
        expected_services: List of expected service names in the pebble plan.
            If None, just verifies at least one layer was added.
    """
    target_containers = [
        c for c in complete_state.containers if c.name == container_name
    ]
    assert target_containers, f"No container '{container_name}' in state"
    container = target_containers[0]

    out = ctx.run(ctx.on.pebble_ready(container), complete_state)
    out_container = out.get_container(container_name)

    if expected_services:
        all_services = {}
        for layer in out_container.layers.values():
            all_services.update(layer.to_dict().get("services", {}))
        for svc in expected_services:
            assert svc in all_services, (
                f"Expected service '{svc}' in pebble plan for {container_name}, "
                f"got: {list(all_services.keys())}"
            )
    else:
        assert (
            out_container.layers
        ), f"Expected pebble layers in {container_name} after pebble-ready"
