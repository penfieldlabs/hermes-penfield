# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Credentials and status: what Hermes sees before a session starts."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support.fake_penfield import FakePenfield

from .conftest import ISOLATION_MODES, TEST_API_KEY, child_env, hermes_cli, run_driver


def _memory_status(home: Path) -> str:
    result = subprocess.run(
        [*hermes_cli(), "memory", "status"],
        env=child_env(home),
        cwd=str(home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    return result.stdout


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_without_credentials_hermes_says_how_to_fix_it(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation, api_key="")

    report = run_driver(home, "unconfigured")

    assert report["loaded"]
    assert report["available"] is False
    assert report["resolved"] is False
    assert any("hermes memory setup" in line for line in report["said"]), report["said"]
    assert fake_api.calls() == []


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_the_api_key_is_read_from_the_profile_not_the_process(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation)

    report = run_driver(home, "profiles")

    assert report["scope_has_key"]
    assert report["environment_has_key"] is False
    assert report["available"], "the provider did not find the profile's key"
    assert report["resolved"]
    assert report["steps"]["tool:store"]["value"].get("id"), report["steps"]["tool:store"]
    (exchange,) = fake_api.calls("POST", "/auth/token")
    assert exchange.authorization == f"Bearer {TEST_API_KEY}"


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_memory_status_describes_the_configuration(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation)

    output = _memory_status(home)

    assert "Provider:  penfield" in output
    assert "available" in output
    assert "not available" not in output
    assert f"api: {fake_api.url}" in output
    assert "credentials: API key" in output
    assert TEST_API_KEY not in output


def test_memory_status_lists_only_the_key_as_missing(
    fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, api_key="")

    output = _memory_status(home)

    assert "not available" in output
    assert "PENFIELD_API_KEY" in output
    missing = output.split("Missing:", 1)[1].split("Note:", 1)[0]
    assert "PENFIELD_URL" not in missing
    assert "PENFIELD_ENV" not in missing


@pytest.mark.parametrize("isolation", ISOLATION_MODES)
def test_setup_asks_for_the_api_key_and_stores_it_as_a_secret(
    isolation: str, fake_api: FakePenfield, make_home: Callable[..., Path]
) -> None:
    home = make_home(api_url=fake_api.url, isolation=isolation, api_key="")

    report = run_driver(home, "setup")

    if report["wizard"] != "ran":
        pytest.skip(f"this Hermes release has a different setup wizard ({report['wizard']})")
    assert report["completed"]
    assert report["asked"] == [{"label": "Penfield API key", "secret": True}]
    assert report["env_writes"] == {"PENFIELD_API_KEY": "tm_pf_setup_ak_entered"}
    assert report["provider_config"] == {}
