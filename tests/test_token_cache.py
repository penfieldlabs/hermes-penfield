# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The on-disk token cache, shared by every Hermes process of a profile."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest
from penfield.auth import PenfieldAuth, TokenSet, key_fingerprint
from penfield.config import Environment, PenfieldConfig
from penfield.exceptions import AuthError

from tests.support.fake_penfield import FakePenfield


def _config(api: FakePenfield, *, api_key: str | None = None) -> PenfieldConfig:
    cfg = PenfieldConfig(env=Environment.DEV, api_key=api_key)
    cfg.api_base = api.url
    return cfg


def _cache(home: Path, tokens: TokenSet) -> Path:
    path = home / "penfield" / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens.to_dict()), encoding="utf-8")
    return path


KEY = "tm_pf_test_ak_unit"


def _issued(api: FakePenfield, *, expires_in: float, key: str | None = None) -> TokenSet:
    """A token pair the fake API recognises, expiring ``expires_in`` seconds from now.

    With ``key`` it is marked as made from that API key, as an exchange does.
    """
    api.valid_access_tokens.add("cached-access")
    api.valid_refresh_tokens.add("cached-refresh")
    return TokenSet(
        "cached-access",
        "cached-refresh",
        expires_at=time.time() + expires_in,
        key_id=key_fingerprint(key) if key else None,
    )


def test_an_expired_cached_token_is_refreshed_before_use(
    api: FakePenfield, tmp_path: Path
) -> None:
    _cache(tmp_path, _issued(api, expires_in=-60, key=KEY))

    header = PenfieldAuth(_config(api, api_key=KEY), tmp_path).get_header()

    assert header != "Bearer cached-access"
    assert header.removeprefix("Bearer ") in api.valid_access_tokens
    assert [c.path for c in api.calls()] == ["/auth/refresh"]


def test_a_second_process_follows_a_token_rotation(api: FakePenfield, tmp_path: Path) -> None:
    """Refresh tokens rotate; a process holding the old one must pick up the new one."""
    _cache(tmp_path, _issued(api, expires_in=3600, key=KEY))
    first = PenfieldAuth(_config(api, api_key=KEY), tmp_path)
    second = PenfieldAuth(_config(api, api_key=KEY), tmp_path)

    first.refresh()
    second.refresh()

    assert second.get_header().removeprefix("Bearer ") in api.valid_access_tokens
    assert [c.path for c in api.calls()] == ["/auth/refresh"]


def test_token_file_is_private_from_the_moment_it_exists(
    api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Permissions come from how the file is created, not from a later chmod."""
    monkeypatch.setattr(Path, "chmod", lambda *_a, **_k: None)
    monkeypatch.setattr(os, "chmod", lambda *_a, **_k: None)
    previous = os.umask(0)
    try:
        PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).authenticate()
    finally:
        os.umask(previous)

    mode = stat.S_IMODE((tmp_path / "penfield" / "tokens.json").stat().st_mode)
    assert mode == 0o600


def test_a_saved_login_the_server_no_longer_accepts_says_what_to_do(
    api: FakePenfield, tmp_path: Path
) -> None:
    from penfield.exceptions import TokenExpiredError

    _cache(tmp_path, TokenSet("dead-access", "dead-refresh", expires_at=time.time() + 3600))
    auth = PenfieldAuth(_config(api), tmp_path)

    with pytest.raises(TokenExpiredError, match="no longer valid: run `hermes penfield login`"):
        auth.refresh()


def _exchanged_keys(api: FakePenfield) -> list[str]:
    return [c.authorization for c in api.calls() if c.path == "/auth/token"]


def test_a_changed_api_key_takes_effect_at_once(api: FakePenfield, tmp_path: Path) -> None:
    """Tokens made from one key are another account's once the key is replaced."""
    PenfieldAuth(_config(api, api_key="tm_pf_first_ak_unit"), tmp_path).get_header()

    PenfieldAuth(_config(api, api_key="tm_pf_second_ak_unit"), tmp_path).get_header()

    assert _exchanged_keys(api) == ["Bearer tm_pf_first_ak_unit", "Bearer tm_pf_second_ak_unit"]


def test_an_api_key_is_used_in_preference_to_a_saved_browser_sign_in(
    api: FakePenfield, tmp_path: Path
) -> None:
    signed_in = _issued(api, expires_in=3600)
    signed_in.source, signed_in.client_id = "oauth", "client-1"
    _cache(tmp_path, signed_in)

    header = PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()

    assert header != "Bearer cached-access"
    assert _exchanged_keys(api) == ["Bearer tm_pf_test_ak_unit"]


def test_processes_with_the_same_key_share_its_tokens(api: FakePenfield, tmp_path: Path) -> None:
    first = PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()

    second = PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()

    assert second == first
    assert len(_exchanged_keys(api)) == 1


def test_a_refreshed_token_still_belongs_to_its_key(api: FakePenfield, tmp_path: Path) -> None:
    auth = PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path)
    auth.get_header()

    refreshed = auth.refresh()
    other = PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()

    assert other == f"Bearer {refreshed.access_token}"
    assert len(_exchanged_keys(api)) == 1


def test_a_cache_from_an_earlier_version_is_replaced_by_one_exchange(
    api: FakePenfield, tmp_path: Path
) -> None:
    """Earlier versions did not record which key a token came from."""
    path = _cache(tmp_path, _issued(api, expires_in=3600))

    PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()
    PenfieldAuth(_config(api, api_key="tm_pf_test_ak_unit"), tmp_path).get_header()

    assert len(_exchanged_keys(api)) == 1
    assert "tm_pf_test_ak_unit" not in path.read_text(encoding="utf-8")


def test_tokens_of_a_key_that_was_removed_are_not_used(api: FakePenfield, tmp_path: Path) -> None:
    """Taking the key out of the profile disconnects it."""
    PenfieldAuth(_config(api, api_key=KEY), tmp_path).get_header()

    without_key = PenfieldAuth(_config(api), tmp_path)

    assert without_key.is_authenticated() is False
    with pytest.raises(AuthError, match="no credentials available"):
        without_key.get_header()


def test_a_saved_sign_in_is_used_when_no_api_key_is_configured(
    api: FakePenfield, tmp_path: Path
) -> None:
    signed_in = _issued(api, expires_in=3600)
    signed_in.source, signed_in.client_id = "oauth", "client-1"
    _cache(tmp_path, signed_in)

    assert PenfieldAuth(_config(api), tmp_path).get_header() == "Bearer cached-access"
    assert _exchanged_keys(api) == []
