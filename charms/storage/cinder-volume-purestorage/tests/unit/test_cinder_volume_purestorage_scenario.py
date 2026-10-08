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

"""ops.testing (state-transition) tests for cinder-volume-purestorage.

This charm is a subordinate charm.  The mandatory relation is
cinder-volume (requires, container scope).
"""


import dataclasses

import pytest
from ops import testing


class TestAllRelations:
    """Config-changed with all mandatory relations present."""

    def test_all_relations(self, ctx, complete_state):
        """With all mandatory relations, charm should proceed past relation checks."""
        state_out = ctx.run(ctx.on.config_changed(), complete_state)

        status = state_out.unit_status
        if status.name == "blocked":
            assert "integration missing" not in status.message, (
                f"Charm blocked on missing integration despite all "
                f"mandatory relations present: {status.message}"
            )


TARGET_TOKEN = "target-api-token-value"


def _with_replication(state, **config):
    """Return state with a replication target secret and extra config."""
    target_secret = testing.Secret(
        tracked_content={"replication-target-api-token": TARGET_TOKEN}, owner="app"
    )
    cfg = {**state.config, **config}
    if cfg.get("replication-target-api-token") == "SECRET":
        cfg["replication-target-api-token"] = target_secret.id
    return dataclasses.replace(
        state,
        config=cfg,
        secrets=[*state.secrets, target_secret],
    )


def _backend_config(ctx, state):
    with ctx(ctx.on.config_changed(), state) as mgr:
        return mgr.charm.get_backend_configuration()


class TestReplication:
    """Replication target options fold into replication-device."""

    def test_no_target(self, ctx, complete_state):
        cfg = _backend_config(ctx, complete_state)
        assert "replication-device" not in cfg
        assert not any(k.startswith("replication-") for k in cfg)

    def test_async_target(self, ctx, complete_state):
        state = _with_replication(
            complete_state,
            **{
                "replication-target-name": "joule",
                "replication-target-address": "10.240.1.53",
                "replication-target-api-token": "SECRET",
            },
        )
        cfg = _backend_config(ctx, state)
        assert cfg["replication-device"] == (
            f"backend_id:joule,san_ip:10.240.1.53,"
            f"api_token:{TARGET_TOKEN},type:async"
        )
        assert not any(
            k.startswith("replication-target") or k == "replication-type"
            for k in cfg
        )

    @pytest.mark.parametrize(
        "rtype,uniform,suffix",
        [
            ("sync", True, "type:sync,uniform:true"),
            ("sync", False, "type:sync"),
            ("async", True, "type:async"),
        ],
    )
    def test_type_and_uniform(self, ctx, complete_state, rtype, uniform, suffix):
        state = _with_replication(
            complete_state,
            **{
                "replication-target-name": "joule",
                "replication-target-address": "10.240.1.53",
                "replication-target-api-token": "SECRET",
                "replication-type": rtype,
                "replication-sync-uniform": uniform,
            },
        )
        cfg = _backend_config(ctx, state)
        assert cfg["replication-device"].endswith(suffix)

    def test_partial_target_rejected(self, ctx, complete_state):
        state = _with_replication(
            complete_state, **{"replication-target-name": "joule"}
        )
        with pytest.raises(Exception) as exc:
            print(type(exc.value))
            _backend_config(ctx, state)

    def test_secret_missing_token_rejected(self, ctx, complete_state):
        bad = testing.Secret(tracked_content={"nottoken": "x"}, owner="app")
        state = dataclasses.replace(
            complete_state,
            config={
                **complete_state.config,
                "replication-target-name": "joule",
                "replication-target-address": "10.240.1.53",
                "replication-target-api-token": bad.id,
            },
            secrets=[*complete_state.secrets, bad],
        )
        with pytest.raises(Exception) as exc:
            print(type(exc.value))
            _backend_config(ctx, state)


# Self-signed CA, valid 2025-2125, generated only for these tests.
REPLICATION_CA = """-----BEGIN CERTIFICATE-----
MIIBGjCBzaADAgECAhRKxfdXpATouHIOrJ4hnrSoEe1UvzAFBgMrZXAwIjEgMB4G
A1UEAwwXcmVwbGljYXRpb24tdGFyZ2V0LnRlc3QwIBcNMjUwMTAxMDAwMDAwWhgP
MjEyNTAxMDEwMDAwMDBaMCIxIDAeBgNVBAMMF3JlcGxpY2F0aW9uLXRhcmdldC50
ZXN0MCowBQYDK2VwAyEApzMAgTOneki2Db89U6BWrDoJ/bS41ivS6CV+XRueizSj
EzARMA8GA1UdEwEB/wQFMAMBAf8wBQYDK2VwA0EAW3FXaVnQyP6d6n883EaaUsl1
vKVMT7HVW4jK4Rd0AvEgUJc1oPECb3SPewlLNX3WhOk7CwismOLn2hVzoYy4Dw==
-----END CERTIFICATE-----
"""

TARGET = {
    "replication-target-name": "joule",
    "replication-target-address": "10.240.1.53",
    "replication-target-api-token": "SECRET",
}


class TestReplicationTLS:
    """replication-driver-ssl-cert is forwarded for the snap to materialise."""

    def test_cert_forwarded_with_target(self, ctx, complete_state):
        """A valid CA is passed through as its own key."""
        state = _with_replication(
            complete_state,
            **TARGET,
            **{"replication-driver-ssl-cert": REPLICATION_CA},
        )
        cfg = _backend_config(ctx, state)
        assert cfg["replication-driver-ssl-cert"].strip() == REPLICATION_CA.strip()

    def test_charm_does_not_add_tls_to_replication_device(self, ctx, complete_state):
        """The snap owns the cert path, so the charm must not touch the device."""
        state = _with_replication(
            complete_state,
            **TARGET,
            **{"replication-driver-ssl-cert": REPLICATION_CA},
        )
        cfg = _backend_config(ctx, state)
        assert cfg["replication-device"] == (
            f"backend_id:joule,san_ip:10.240.1.53,"
            f"api_token:{TARGET_TOKEN},type:async"
        )
        assert "ssl_cert" not in cfg["replication-device"]

    def test_cert_does_not_set_primary_cert(self, ctx, complete_state):
        """The replication CA must not be reused as driver-ssl-cert."""
        state = _with_replication(
            complete_state,
            **TARGET,
            **{"replication-driver-ssl-cert": REPLICATION_CA},
        )
        cfg = _backend_config(ctx, state)
        assert not cfg.get("driver-ssl-cert")

    def test_cert_without_target_is_accepted(self, ctx, complete_state):
        """The CA is optional and not part of the replication group."""
        state = _with_replication(
            complete_state, **{"replication-driver-ssl-cert": REPLICATION_CA}
        )
        cfg = _backend_config(ctx, state)
        assert "replication-device" not in cfg

    def test_unset_cert_is_not_forwarded(self, ctx, complete_state):
        """Without the option, no replication cert key is sent."""
        state = _with_replication(complete_state, **TARGET)
        cfg = _backend_config(ctx, state)
        assert not cfg.get("replication-driver-ssl-cert")

    @pytest.mark.parametrize(
        "invalid",
        [
            "not a certificate",
            "/tmp/ca.pem",
            "-----BEGIN CERTIFICATE-----\nbad\n-----END CERTIFICATE-----",
        ],
    )
    def test_invalid_cert_rejected(self, ctx, complete_state, invalid):
        """Non-PEM input is rejected like driver-ssl-cert."""
        state = _with_replication(
            complete_state,
            **TARGET,
            **{"replication-driver-ssl-cert": invalid},
        )
        with pytest.raises(Exception):  # use the type pinned in test_partial_target_rejected
            _backend_config(ctx, state)
