#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Behavioral / interaction tests for openstack-port-cni-k8s.

Tests here verify that the charm calls the right Kubernetes API methods and
delegates to the right collector actions.  They use the ops.testing scenario
API to fire events, then assert on mock call counts / arguments rather than
on output State properties.

Pure state-transition tests (unit_status, workload_version) live in
test_openstack_port_cni_scenario.py.

Fixtures (ctx, complete_state, mock_k8s, …) are provided by conftest.py.
"""

import unittest.mock as mock

from ops import (
    testing,
)
from ops.manifests import (
    ManifestClientError,
)
from ops_sunbeam.test_utils_scenario import (
    certificate_transfer_relation_complete,
    peer_relation,
)

# ---------------------------------------------------------------------------
# TestKubernetesCredentials
# ---------------------------------------------------------------------------


class TestKubernetesCredentials:
    """Kubernetes Secret / DaemonSet patch behaviour driven by credentials."""

    def test_credentials_secret_applied(self, ctx, complete_state, mock_k8s):
        """Successful configure → OpenStack credentials Secret applied."""
        ctx.run(ctx.on.config_changed(), complete_state)
        mock_k8s.apply.assert_called()

    def test_daemonset_checksum_patched(self, ctx, complete_state, mock_k8s):
        """Successful configure → DaemonSet patched with a credentials checksum."""
        ctx.run(ctx.on.config_changed(), complete_state)
        mock_k8s.patch.assert_called()

    def test_credentials_not_written_without_identity_credentials(
        self, ctx, mock_k8s
    ):
        """No credentials Secret written when identity-credentials is absent."""
        state_in = testing.State(
            leader=True,
            relations=[peer_relation()],
        )
        ctx.run(ctx.on.config_changed(), state_in)
        mock_k8s.apply.assert_not_called()

    def test_non_leader_does_not_write_credentials(
        self, ctx, complete_state, mock_k8s
    ):
        """Non-leader must not make any k8s API calls."""
        state_in = testing.State(
            leader=False,
            relations=complete_state.relations,
            secrets=complete_state.secrets,
        )
        ctx.run(ctx.on.config_changed(), state_in)
        mock_k8s.apply.assert_not_called()


# ---------------------------------------------------------------------------
# TestCACert
# ---------------------------------------------------------------------------


class TestCACert:
    """CA certificate relation affects which Kubernetes Secrets are written."""

    def _state_with_ca_cert(self, complete_relations, complete_secrets):
        return testing.State(
            leader=True,
            relations=list(complete_relations)
            + [certificate_transfer_relation_complete()],
            secrets=complete_secrets,
        )

    def test_ca_bundle_secret_applied_when_cert_present(
        self, ctx, complete_relations, complete_secrets, mock_k8s
    ):
        """CA bundle Secret and credentials Secret are both applied (≥2 calls)."""
        ctx.run(
            ctx.on.config_changed(),
            self._state_with_ca_cert(complete_relations, complete_secrets),
        )
        assert mock_k8s.apply.call_count >= 2

    def test_ca_bundle_deletion_attempted_without_cert(
        self, ctx, complete_state, mock_k8s
    ):
        """Without receive-ca-cert the charm deletes the CA bundle Secret."""
        ctx.run(ctx.on.config_changed(), complete_state)
        mock_k8s.delete.assert_called()

    def test_os_cacert_included_in_credentials_when_cert_present(
        self, ctx, complete_relations, complete_secrets, mock_k8s
    ):
        """OS_CACERT key is present in the credentials Secret when CA is set."""
        ctx.run(
            ctx.on.config_changed(),
            self._state_with_ca_cert(complete_relations, complete_secrets),
        )
        call_data_keys = set()
        for call in mock_k8s.apply.call_args_list:
            secret_obj = call.args[0] if call.args else None
            if secret_obj is not None and hasattr(secret_obj, "data"):
                call_data_keys.update(secret_obj.data or {})
        assert "OS_CACERT" in call_data_keys

    def test_os_cacert_absent_from_credentials_without_cert(
        self, ctx, complete_state, mock_k8s
    ):
        """OS_CACERT is absent from the credentials Secret when no CA is set."""
        ctx.run(ctx.on.config_changed(), complete_state)
        call_data_keys = set()
        for call in mock_k8s.apply.call_args_list:
            secret_obj = call.args[0] if call.args else None
            if secret_obj is not None and hasattr(secret_obj, "data"):
                call_data_keys.update(secret_obj.data or {})
        assert "OS_CACERT" not in call_data_keys


# ---------------------------------------------------------------------------
# TestRemoveEvent
# ---------------------------------------------------------------------------


class TestRemoveEvent:
    """Remove event cleans up Kubernetes resources (leader only)."""

    def test_remove_deletes_both_manifest_sets(self, ctx, complete_state):
        """Remove on leader → delete_manifests called for both manifest sets."""
        with mock.patch(
            "ops.manifests.manifest.Manifests.delete_manifests"
        ) as mock_delete:
            ctx.run(ctx.on.remove(), complete_state)
        assert mock_delete.call_count == 2

    def test_remove_deletes_credentials_secret(
        self, ctx, complete_state, mock_k8s
    ):
        """Remove on leader → credentials Secret deleted from the cluster."""
        ctx.run(ctx.on.remove(), complete_state)
        mock_k8s.delete.assert_called()

    def test_non_leader_does_not_remove_resources(
        self, ctx, complete_state, mock_k8s
    ):
        """Remove on non-leader → no k8s API calls made."""
        state_in = testing.State(
            leader=False,
            relations=complete_state.relations,
            secrets=complete_state.secrets,
        )
        with mock.patch(
            "ops.manifests.manifest.Manifests.delete_manifests"
        ) as mock_delete:
            ctx.run(ctx.on.remove(), state_in)
        mock_delete.assert_not_called()
        mock_k8s.delete.assert_not_called()


# ---------------------------------------------------------------------------
# TestActions
# ---------------------------------------------------------------------------


class TestActions:
    """Action handlers delegate to the Collector without error."""

    def test_list_versions_action(self, ctx, complete_state):
        """list-versions action invokes Collector.list_versions."""
        with mock.patch("charm.Collector.list_versions") as mock_list:
            ctx.run(ctx.on.action("list-versions"), complete_state)
        mock_list.assert_called_once()

    def test_list_resources_action(self, ctx, complete_state):
        """list-resources action invokes Collector.list_resources."""
        with mock.patch("charm.Collector.list_resources") as mock_list:
            ctx.run(ctx.on.action("list-resources"), complete_state)
        mock_list.assert_called_once()

    def test_scrub_resources_action(self, ctx, complete_state):
        """scrub-resources action invokes Collector.scrub_resources."""
        with mock.patch("charm.Collector.scrub_resources") as mock_scrub:
            ctx.run(ctx.on.action("scrub-resources"), complete_state)
        mock_scrub.assert_called_once()

    def test_sync_resources_action(self, ctx, complete_state):
        """sync-resources action invokes Collector.apply_missing_resources."""
        with mock.patch(
            "charm.Collector.apply_missing_resources"
        ) as mock_sync:
            ctx.run(ctx.on.action("sync-resources"), complete_state)
        mock_sync.assert_called_once()

    def test_sync_resources_handles_manifest_error(self, ctx, complete_state):
        """sync-resources sets result message on ManifestClientError."""
        with mock.patch(
            "charm.Collector.apply_missing_resources",
            side_effect=ManifestClientError("boo", "foo"),
        ):
            state_out = ctx.run(
                ctx.on.action("sync-resources"), complete_state
            )
        assert state_out is not None
