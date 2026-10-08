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
        tracked_content={"token": TARGET_TOKEN}, owner="app"
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
