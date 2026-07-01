# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Load the root directory as a package called 'penfield' so relative imports work.
# This mirrors how Hermes loads it (spec_from_file_location + submodule_search_locations).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_penfield_pkg() -> None:
    if "penfield" in sys.modules:
        return
    init_file = _REPO_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "penfield",
        str(init_file),
        submodule_search_locations=[str(_REPO_ROOT)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["penfield"] = mod
    spec.loader.exec_module(mod)


_load_penfield_pkg()

# Now tests can do: from penfield.auth import PenfieldAuth etc.
from penfield.auth import PenfieldAuth, TokenSet  # noqa: E402
from penfield.client import PenfieldClient  # noqa: E402
from penfield.config import Environment, PenfieldConfig  # noqa: E402

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
            "User-Agent": "hermes-penfield/0.2.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return str(data["data"]["access_token"])


@pytest.fixture
def live_client(live_jwt: str) -> PenfieldClient:
    from penfield.config import Environment, PenfieldConfig

    cfg = PenfieldConfig(env=Environment.DEV, api_key="unused")
    cfg.api_base = "https://api-dev.penfield.app/api/v2"
    ts = TokenSet(
        access_token=live_jwt,
        refresh_token="unused",
        expires_at=9_999_999_999.0,
    )
    auth: PenfieldAuth = PenfieldAuth(cfg, tokens=ts)
    return PenfieldClient(auth, cfg)
