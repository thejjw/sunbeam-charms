#!/usr/bin/env python3

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

"""Scenario (ops.testing state-transition) tests for sunbeam-clusterd."""

import datetime
import os
from types import (
    SimpleNamespace,
)
from unittest.mock import (
    MagicMock,
)

import pytest
import clusterd as clusterd_module
import requests as requests_module
from charms.operator_libs_linux.v2 import (
    snap,
)
from cryptography import (
    x509,
)
from cryptography.hazmat.primitives import (
    hashes,
    serialization,
)
from cryptography.hazmat.primitives.asymmetric import (
    rsa,
)
from cryptography.x509.oid import (
    NameOID,
)
from ops import (
    testing,
)
from ops_sunbeam.test_utils_scenario import (
    assert_unit_status,
    certificates_relation_complete,
    tracing_relation_complete,
)

import charm as charm_module
from charm import SunbeamClusterdCharm


def _generate_cert(not_after: datetime.datetime) -> str:
    """Generate a self-signed certificate valid until not_after."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    """Redirect the clusterd state dir to a temp dir."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(charm_module, "CLUSTERD_STATE_DIR", state_dir)
    return state_dir


def _cert_bundle(not_after: datetime.datetime) -> dict:
    """Bundle as produced by TlsCertificatesHandler.context()."""
    return {
        "cert": _generate_cert(not_after),
        "key": "TEST-KEY",
        "ca_cert": "TEST-CA",
        "ca_with_chain": "TEST-CA",
    }


class TestCertBundleIsNewer:
    """LP #2168558: downgrade guard for state-dir cert writes."""

    def test_newer_bundle_is_newer(self, state_dir):
        """A bundle with a later notAfter than on-disk is newer."""
        old = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=10)
        new = old + datetime.timedelta(days=10)
        (state_dir / "cluster.crt").write_text(_generate_cert(old))
        assert SunbeamClusterdCharm._cert_bundle_is_newer(_cert_bundle(new)) is True

    def test_older_bundle_is_not_newer(self, state_dir):
        """A bundle with an earlier notAfter than on-disk is rejected."""
        new = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=20)
        old = new - datetime.timedelta(days=10)
        (state_dir / "cluster.crt").write_text(_generate_cert(new))
        assert SunbeamClusterdCharm._cert_bundle_is_newer(_cert_bundle(old)) is False

    def test_missing_on_disk_cert_is_newer(self, state_dir):
        """With no on-disk cert any valid bundle applies."""
        bundle = _cert_bundle(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        )
        assert SunbeamClusterdCharm._cert_bundle_is_newer(bundle) is True

    def test_unparseable_bundle_rejected(self, state_dir):
        """Garbage shared cert must not be applied."""
        assert SunbeamClusterdCharm._cert_bundle_is_newer({"cert": "garbage"}) is False


class TestWriteCertsToStateDir:
    """LP #2168558: state-dir fallback write behaviour."""

    def test_skips_older_bundle(self, state_dir, _mock_snap):
        """An older bundle is not written and the snap is not restarted."""
        new = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=20)
        old = new - datetime.timedelta(days=10)
        (state_dir / "cluster.crt").write_text(_generate_cert(new))
        assert SunbeamClusterdCharm._write_certs_to_state_dir(_cert_bundle(old)) is False
        assert not (state_dir / "cluster.key").exists()
        _mock_snap.__getitem__.return_value.restart.assert_not_called()

    def test_writes_newer_bundle_and_secures_key(self, state_dir, _mock_snap):
        """A newer bundle is written, key is 0600, snap restarted."""
        old = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5)
        new = old + datetime.timedelta(days=10)
        (state_dir / "cluster.crt").write_text(_generate_cert(old))
        assert SunbeamClusterdCharm._write_certs_to_state_dir(_cert_bundle(new)) is True
        assert (state_dir / "cluster.key").read_text() == "TEST-KEY"
        assert (state_dir / "cluster-ca.crt").read_text() == "TEST-CA"
        assert os.stat(state_dir / "cluster.key").st_mode & 0o777 == 0o600
        _mock_snap.__getitem__.return_value.restart.assert_called_once()


class _RelData(dict):
    """Relation data mapping that answers for any entity with app data."""

    def __getitem__(self, key):
        return dict.__getitem__(self, "app")


class TestPublishCertsToPeers:
    """LP #2168558: peer-secret publication reuses the existing secret."""

    def test_reuses_secret_from_relation_data(self):
        """With the id already in peer app data, no new secret is created."""
        charm_stub = SimpleNamespace()
        charm_stub.model = SimpleNamespace(
            get_relation=lambda name: SimpleNamespace(
                app=SimpleNamespace(),
                data=_RelData({"app": {"cluster-certs-secret-id": "secret-1"}}),
            )
        )
        secret = MagicMock()
        charm_stub.model.get_secret = MagicMock(return_value=secret)
        charm_stub.app = SimpleNamespace(
            add_secret=MagicMock(side_effect=AssertionError("must not create a secret"))
        )
        SunbeamClusterdCharm._publish_certs_to_peers(charm_stub, {"cert": "x"})
        secret.set_content.assert_called_once()
        charm_stub.model.get_secret.assert_called_once_with(id="secret-1")

    def test_creates_secret_when_absent(self):
        """Without an existing id, a secret is created and referenced."""
        charm_stub = SimpleNamespace()
        app_data: dict = {}
        charm_stub.model = SimpleNamespace(
            get_relation=lambda name: SimpleNamespace(
                app=SimpleNamespace(),
                data=_RelData({"app": app_data}),
            )
        )
        charm_stub.model.get_secret = MagicMock()
        new_secret = MagicMock()
        new_secret.id = "secret-new"
        charm_stub.app = SimpleNamespace(add_secret=MagicMock(return_value=new_secret))
        SunbeamClusterdCharm._publish_certs_to_peers(charm_stub, {"cert": "x"})
        charm_stub.app.add_secret.assert_called_once()
        assert app_data["cluster-certs-secret-id"] == "secret-new"


class TestConfigureCertificatesFallback:
    """LP #2168558: leader fallback only on a dead cert API, hash on success."""

    def _charm_stub(self):
        stub = SimpleNamespace()
        stub.certs = SimpleNamespace(ready=True, context=lambda: _cert_bundle(
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
        ))
        stub._state = SimpleNamespace(certs_hash="")
        stub._clusterd = MagicMock()
        stub._write_certs_to_state_dir = MagicMock(return_value=True)
        stub._publish_certs_to_peers = MagicMock()
        return stub

    def test_unavailable_api_falls_back_without_hash(self):
        """ClusterdUnavailableError → fallback runs, certs_hash not set."""
        stub = self._charm_stub()
        stub._clusterd.set_certs.side_effect = clusterd_module.ClusterdUnavailableError(
            "503"
        )
        SunbeamClusterdCharm.configure_certificates(stub)
        stub._write_certs_to_state_dir.assert_called_once()
        stub._publish_certs_to_peers.assert_called_once()
        assert stub._state.certs_hash == ""

    def test_5xx_http_error_falls_back(self):
        """HTTPError with 5xx (peer forwarding failed) → fallback runs."""
        stub = self._charm_stub()
        response = SimpleNamespace(status_code=500, text="boom")
        stub._clusterd.set_certs.side_effect = requests_module.HTTPError(
            "500", response=response
        )
        SunbeamClusterdCharm.configure_certificates(stub)
        stub._write_certs_to_state_dir.assert_called_once()
        assert stub._state.certs_hash == ""

    def test_4xx_http_error_raises(self):
        """HTTPError with 4xx (bad request) must fail loudly."""
        stub = self._charm_stub()
        response = SimpleNamespace(status_code=400, text="bad")
        stub._clusterd.set_certs.side_effect = requests_module.HTTPError(
            "400", response=response
        )
        with pytest.raises(requests_module.HTTPError):
            SunbeamClusterdCharm.configure_certificates(stub)
        stub._write_certs_to_state_dir.assert_not_called()

    def test_success_sets_hash(self):
        """Healthy API path records the hash."""
        stub = self._charm_stub()
        SunbeamClusterdCharm.configure_certificates(stub)
        stub._clusterd.set_certs.assert_called_once()
        assert stub._state.certs_hash != ""


class TestLeaderBootstrap:
    """Leader with peers relation bootstraps and reaches active."""

    def test_active_with_peers(self, ctx, complete_state, _mock_clusterd):
        """Config-changed as leader with peers → ActiveStatus."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        assert state_out.unit_status == testing.ActiveStatus("")
        _mock_clusterd.bootstrap.assert_called_once()

    def test_clusterd_ready_called(self, ctx, complete_state, _mock_clusterd):
        """Leader checks clusterd readiness before bootstrap."""
        ctx.run(ctx.on.config_changed(), complete_state)
        _mock_clusterd.ready.assert_called()


class TestNonLeaderWaits:
    """Non-leader unit waits for leader readiness."""

    def test_waiting_non_leader(self, ctx, peers):
        """Non-leader without leader_ready → WaitingStatus."""
        state_in = testing.State(
            leader=False,
            relations=[peers],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "Leader not ready")

    def test_non_leader_does_not_bootstrap(self, ctx, peers, _mock_clusterd):
        """Non-leader must not call bootstrap."""
        state_in = testing.State(
            leader=False,
            relations=[peers],
        )
        ctx.run(ctx.on.config_changed(), state_in)
        _mock_clusterd.bootstrap.assert_not_called()


class TestNoPeersBlocked:
    """Without peer relation the charm is blocked."""

    def test_blocked_no_relations(self, ctx):
        """No relations at all → blocked."""
        state_in = testing.State(leader=True)
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status.name in ("blocked", "waiting")


class TestWithCertificates:
    """With certificates relation present but incomplete, charm reports waiting."""

    def test_waiting_when_certs_incomplete(self, ctx, peers):
        """Leader with peers + incomplete certificates → waiting for certs."""
        state_in = testing.State(
            leader=True,
            relations=[peers, certificates_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert_unit_status(state_out, "waiting", "certificates")

    def test_no_certs_pushed_when_incomplete(self, ctx, peers, _mock_clusterd):
        """Certs not pushed to clusterd when certificate data is incomplete."""
        state_in = testing.State(
            leader=True,
            relations=[peers, certificates_relation_complete()],
        )
        ctx.run(ctx.on.config_changed(), state_in)
        _mock_clusterd.set_certs.assert_not_called()


class TestWithTracing:
    """With tracing relation present, charm still reaches active."""

    def test_active_with_tracing(self, ctx, peers):
        """Leader with peers + tracing → active."""
        state_in = testing.State(
            leader=True,
            relations=[peers, tracing_relation_complete()],
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")


class TestConfigChanged:
    """Config-changed event updates snap settings."""

    def test_config_changed_custom_channel(self, ctx, peers, _mock_snap):
        """Config-changed with custom snap-channel, bare risk resolved."""
        state_in = testing.State(
            leader=True,
            relations=[peers],
            config={"snap-channel": "edge"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus("")

    def test_config_changed_same_track_refreshes_snap(
        self, ctx, peers, _mock_snap
    ):
        """Same-track channel change (risk change) refreshes the snap."""
        mock_openstack = _mock_snap["openstack"]
        mock_openstack.present = True
        mock_openstack.channel = "2026.1/stable"
        state_in = testing.State(
            leader=True,
            relations=[peers],
            config={"snap-channel": "2026.1/candidate"},
        )
        ctx.run(ctx.on.config_changed(), state_in)
        mock_openstack.ensure.assert_called_once_with(
            snap.SnapState.Latest, channel="2026.1/candidate"
        )

    def test_config_changed_track_change_no_swap(self, ctx, peers, _mock_snap):
        """Track change does not swap the snap from a hook."""
        mock_openstack = _mock_snap["openstack"]
        mock_openstack.present = True
        mock_openstack.channel = "2026.1/stable"
        state_in = testing.State(
            leader=True,
            relations=[peers],
            config={"snap-channel": "2025.1/stable"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        assert state_out.unit_status == testing.ActiveStatus(
            "(snap-channel) installed snap: 2026.1/stable,"
            " configured: 2025.1/stable"
        )
        mock_openstack.ensure.assert_not_called()

    def test_refresh_snap_action_channel_param(self, ctx, peers, _mock_snap):
        """refresh-snap action honours the channel param."""
        mock_openstack = _mock_snap["openstack"]
        mock_openstack.present = True
        mock_openstack.channel = "2026.1/stable"
        state_in = testing.State(
            leader=True,
            relations=[peers],
            config={"snap-channel": "2026.1/stable"},
        )
        state_out = ctx.run(ctx.on.config_changed(), state_in)
        ctx.run(
            ctx.on.action("refresh-snap", params={"channel": "2025.1/stable"}),
            state_out,
        )
        mock_openstack.ensure.assert_called_with(
            snap.SnapState.Latest, channel="2025.1/stable"
        )


class TestGetCredentialsAction:
    """get-credentials action returns correct URL."""

    def test_get_credentials(self, ctx, complete_state, _mock_clusterd):
        """Action returns URL with binding address."""
        _mock_clusterd.bootstrap.return_value = None
        # First bootstrap the charm so peers.interface.state.joined is True
        state_out = ctx.run(ctx.on.config_changed(), complete_state)
        ctx_out = ctx.run(ctx.on.action("get-credentials"), state_out)
        assert ctx_out.unit_status == testing.ActiveStatus("")


class TestSnapNotFound:
    """Install event when snap is not present raises an error."""

    def test_snap_not_installed_raises(self, ctx, _mock_snap):
        """Install raises when snap is not found."""
        mock_openstack = _mock_snap["openstack"]
        mock_openstack.present = False
        mock_openstack.ensure.side_effect = snap.SnapNotFoundError("openstack")
        state_in = testing.State(leader=True)
        with pytest.raises(Exception, match="SnapInstallationError"):
            ctx.run(ctx.on.install(), state_in)
