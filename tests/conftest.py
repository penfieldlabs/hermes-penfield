# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Shared pytest fixtures.

Unit tests never touch the network. Integration tests (marked
``@pytest.mark.integration``) hit the live dev API and are skipped unless
``PENFIELD_API_KEY`` and ``PENFIELD_ENV=dev`` are set — see ADR-0011.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from hermes_penfield.auth import PenfieldAuth
from hermes_penfield.client import PenfieldClient
from hermes_penfield.config import Environment, PenfieldConfig


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: needs live dev API")
    # Surface dev-readiness in the session header (belt-and-braces; the real
    # gate is _integration_ready() in the live fixtures below).
    env = os.environ.get("PENFIELD_ENV", "<unset>")
    key_status = "set" if os.environ.get("PENFIELD_API_KEY") else "<unset>"
    if not _integration_ready():
        print(
            f"\n[integration] PENFIELD_ENV={env!r} PENFIELD_API_KEY={key_status}; "
            "integration tests will be skipped (dev-only, requires both)."
        )


# ---------------------------------------------------------------------------
# Unit fixtures (no network)
# ---------------------------------------------------------------------------


@pytest.fixture
def dev_config() -> PenfieldConfig:
    return PenfieldConfig(env=Environment.DEV)


@pytest.fixture
def prod_config() -> PenfieldConfig:
    return PenfieldConfig(env=Environment.PROD)


class FakeAuth:
    """Drop-in for PenfieldAuth that never hits the network."""

    def __init__(self, header: str = "Bearer test-token") -> None:
        self._header = header
        self.refresh_calls = 0

    def get_header(self) -> str:
        return self._header

    def refresh(self) -> Any:
        self.refresh_calls += 1
        return None

    def is_authenticated(self) -> bool:
        return True


@pytest.fixture
def fake_auth() -> FakeAuth:
    return FakeAuth()


@pytest.fixture
def client(fake_auth: FakeAuth, dev_config: PenfieldConfig) -> PenfieldClient:
    return PenfieldClient(fake_auth, dev_config, sleep=lambda _s: None)


def _integration_ready() -> bool:
    """True only when explicitly pointed at DEV with a key present."""
    env = os.environ.get("PENFIELD_ENV", "").lower()
    key = os.environ.get("PENFIELD_API_KEY", "")
    # Hard refusal of prod: even if someone sets the key, dev is required.
    return bool(key) and env == "dev"


# ---------------------------------------------------------------------------
# Integration fixtures (live dev API)
# ---------------------------------------------------------------------------
#
# INVARIANT: live integration tests may ONLY run against the dev environment.
# They create and delete real memories in a real tenant. Two independent
# gates enforce this — if either fails, every integration test is skipped:
#
#   1. PENFIELD_ENV must be exactly "dev" (never prod, never unset).
#   2. PENFIELD_API_KEY must be set.
#
# Additionally, tests that *create* memories gate on the `can_delete` fixture
# so they never leave orphan junk (see test_integration.py). No creation
# without guaranteed cleanup. See ADR-0011.


@pytest.fixture(scope="session")
def live_jwt() -> str:
    if not _integration_ready():
        pytest.skip("integration tests need PENFIELD_API_KEY + PENFIELD_ENV=dev")
    import urllib.request

    key = os.environ["PENFIELD_API_KEY"]
    req = urllib.request.Request(
        "https://api-dev.penfield.app/api/v2/auth/token",
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Cloudflare (1010) blocks bare urllib UA; send the plugin UA.
            "User-Agent": "hermes-penfield/0.1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return str(data["data"]["access_token"])


@pytest.fixture
def live_client(live_jwt: str) -> PenfieldClient:
    """A client hard-pinned to DEV, authenticated with a fresh JWT.

    Belt-and-braces: even if env vars were mutated mid-session, this client's
    config is constructed with ``Environment.DEV`` and an explicit dev URL,
    so it physically cannot reach prod. Integration tests must use this.
    """
    # Final refusal: never construct a live client if we're not on dev.
    if os.environ.get("PENFIELD_ENV", "").lower() != "dev":
        pytest.skip("live_client refuses to construct without PENFIELD_ENV=dev")
    from hermes_penfield.auth import TokenSet

    cfg = PenfieldConfig(env=Environment.DEV, api_key="unused")
    cfg.api_base = "https://api-dev.penfield.app/api/v2"
    ts = TokenSet(
        access_token=live_jwt,
        refresh_token="unused",
        expires_at=9_999_999_999.0,
    )
    auth: PenfieldAuth = PenfieldAuth(cfg, tokens=ts)
    return PenfieldClient(auth, cfg)
