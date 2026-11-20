# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Shared pytest fixtures."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from collections.abc import Iterator
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
from penfield.constants import USER_AGENT  # noqa: E402
from penfield.provider import PenfieldMemoryProvider  # noqa: E402

from tests.support.fake_penfield import FakePenfield  # noqa: E402

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


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    """The Hermes home unit tests run under (see ``_isolated_environment``)."""
    return tmp_path / "hermes-home"


@pytest.fixture(autouse=True)
def _isolated_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, hermes_home: Path
) -> None:
    """Keep unit tests away from the developer's real credentials and home."""
    if "integration" in request.keywords:
        return
    for name in ("PENFIELD_API_KEY", "PENFIELD_URL", "PENFIELD_ENV"):
        monkeypatch.delenv(name, raising=False)
    hermes_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))


# ---------------------------------------------------------------------------
# Local API fixtures (real HTTP against an in-memory fake on loopback)
# ---------------------------------------------------------------------------


@pytest.fixture
def api() -> Iterator[FakePenfield]:
    """A running fake Penfield API that accepts ``FakeAuth``'s token."""
    with FakePenfield() as fake:
        fake.valid_access_tokens.add("test-token")
        yield fake


@pytest.fixture
def api_client(api: FakePenfield, fake_auth: FakeAuth) -> PenfieldClient:
    """A real client pointed at the fake API; backoff sleeps are skipped."""
    cfg = PenfieldConfig(env=Environment.DEV)
    cfg.api_base = api.url
    return PenfieldClient(fake_auth, cfg, sleep=lambda _s: None)


@pytest.fixture
def provider(
    api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> PenfieldMemoryProvider:
    """A provider initialized the way Hermes does it, logged in to the fake API."""
    monkeypatch.setenv("PENFIELD_URL", api.url)
    monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
    instance = PenfieldMemoryProvider()
    instance.initialize("unit-session", hermes_home=str(tmp_path), platform="cli")
    return instance


# ---------------------------------------------------------------------------
# Live fixtures (the real Penfield API; see tests/test_integration.py)
# ---------------------------------------------------------------------------


def _live_environment() -> Environment | None:
    """The environment the live tests may use, or ``None`` to skip them.

    They write to the account, so only ``dev`` is accepted unless the
    caller also sets ``PENFIELD_TEST_ALLOW_PROD=1``.
    """
    if not os.environ.get("PENFIELD_API_KEY"):
        return None
    name = os.environ.get("PENFIELD_ENV", "").lower()
    if name == "dev":
        return Environment.DEV
    if name == "prod" and os.environ.get("PENFIELD_TEST_ALLOW_PROD") == "1":
        return Environment.PROD
    return None


@pytest.fixture(scope="session")
def live_config() -> PenfieldConfig:
    environment = _live_environment()
    if environment is None:
        pytest.skip("live tests need PENFIELD_API_KEY and PENFIELD_ENV=dev")
    return PenfieldConfig(env=environment, api_key="unused")


@pytest.fixture(scope="session")
def live_jwt(live_config: PenfieldConfig) -> str:
    """An access token from a real API-key exchange."""
    import urllib.request

    req = urllib.request.Request(
        f"{live_config.api_base}/auth/token",
        method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['PENFIELD_API_KEY']}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    return str(data["data"]["access_token"])


@pytest.fixture
def live_client(live_config: PenfieldConfig, live_jwt: str) -> PenfieldClient:
    tokens = TokenSet(access_token=live_jwt, refresh_token="unused", expires_at=9_999_999_999.0)
    return PenfieldClient(PenfieldAuth(live_config, tokens=tokens), live_config)
