# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Shared pytest fixtures.

Unit tests never touch the network. Integration tests (marked
``@pytest.mark.integration``) hit the live dev API and are skipped unless
``PENFIELD_API_KEY`` and ``PENFIELD_ENV=dev`` are set — see ADR-0015.
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


# ---------------------------------------------------------------------------
# Integration fixtures (live dev API)
# ---------------------------------------------------------------------------


def _integration_ready() -> bool:
    return (
        bool(os.environ.get("PENFIELD_API_KEY"))
        and os.environ.get("PENFIELD_ENV", "").lower() == "dev"
    )


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
    """A client whose auth is a fixed JWT (no refresh path exercised)."""
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
