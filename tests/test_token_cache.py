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
from penfield.auth import PenfieldAuth, TokenSet, key_fingerprint, saved_browser_sign_in
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
        api_base=api.url,
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

    with pytest.raises(
        TokenExpiredError, match=r"no longer valid.*run `hermes memory setup penfield`"
    ):
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
    with pytest.raises(AuthError, match="Penfield is not connected"):
        without_key.get_header()


def test_a_saved_sign_in_is_used_when_no_api_key_is_configured(
    api: FakePenfield, tmp_path: Path
) -> None:
    signed_in = _issued(api, expires_in=3600)
    signed_in.source, signed_in.client_id = "oauth", "client-1"
    _cache(tmp_path, signed_in)

    assert PenfieldAuth(_config(api), tmp_path).get_header() == "Bearer cached-access"
    assert _exchanged_keys(api) == []


# ---------------------------------------------------------------------------
# Tokens belong to the address that issued them
# ---------------------------------------------------------------------------


def _sign_in_at(api: FakePenfield, home: Path) -> dict[str, str]:
    """Sign in with a browser at ``api`` and return the cache it leaves."""
    api.approve_sign_ins = True
    PenfieldAuth(_config(api), home).device_code_flow()
    saved: dict[str, str] = json.loads((home / "penfield" / "tokens.json").read_text())
    return saved


def _credentials_seen(api: FakePenfield) -> str:
    """Everything that could carry a login in the requests ``api`` received."""
    return " ".join(f"{c.authorization} {c.body}" for c in api.calls())


def test_the_cache_records_the_address_the_tokens_were_issued_for(
    api: FakePenfield, tmp_path: Path
) -> None:
    assert _sign_in_at(api, tmp_path)["api_base"] == api.url

    key_home = tmp_path / "key"
    PenfieldAuth(_config(api, api_key=KEY), key_home).get_header()
    assert json.loads((key_home / "penfield" / "tokens.json").read_text())["api_base"] == api.url


def test_a_sign_in_is_not_sent_to_another_address(api: FakePenfield, tmp_path: Path) -> None:
    """Point the profile somewhere else and the saved sign-in stays at home."""
    saved = _sign_in_at(api, tmp_path)

    with FakePenfield() as elsewhere:
        auth = PenfieldAuth(_config(elsewhere), tmp_path)
        assert auth.is_authenticated() is False
        with pytest.raises(AuthError, match="Penfield is not connected"):
            auth.get_header()
        assert elsewhere.calls() == []
        assert saved_browser_sign_in(tmp_path, elsewhere.url) is None

    assert saved_browser_sign_in(tmp_path, api.url) is not None
    assert json.loads((tmp_path / "penfield" / "tokens.json").read_text()) == saved


def test_tokens_of_an_api_key_are_not_sent_to_another_address(
    api: FakePenfield, tmp_path: Path
) -> None:
    """The key itself is the login there; tokens issued here are not."""
    PenfieldAuth(_config(api, api_key=KEY), tmp_path).get_header()
    issued_here = json.loads((tmp_path / "penfield" / "tokens.json").read_text())

    with FakePenfield() as elsewhere:
        header = PenfieldAuth(_config(elsewhere, api_key=KEY), tmp_path).get_header()

        assert [c.path for c in elsewhere.calls()] == ["/auth/token"]
        assert header.removeprefix("Bearer ") in elsewhere.valid_access_tokens
        seen = _credentials_seen(elsewhere)
        assert issued_here["access_token"] not in seen
        assert issued_here["refresh_token"] not in seen


def test_logging_out_revokes_a_sign_in_where_it_was_issued(
    api: FakePenfield, tmp_path: Path
) -> None:
    saved = _sign_in_at(api, tmp_path)

    with FakePenfield() as elsewhere:
        assert PenfieldAuth(_config(elsewhere), tmp_path).logout() is True
        assert elsewhere.calls() == []

    assert api.revoked == [saved["refresh_token"], saved["access_token"]]
    assert not (tmp_path / "penfield" / "tokens.json").exists()


class TestACacheWrittenBeforeTheAddressWasRecorded:
    """Earlier versions did not record it. Those logins were made at Penfield's own API."""

    def _legacy(self, home: Path) -> None:
        tokens = TokenSet("legacy-access", "legacy-refresh", time.time() + 3600, source="oauth")
        assert "api_base" not in tokens.to_dict()
        _cache(home, tokens)

    @pytest.mark.parametrize("env", [Environment.PROD, Environment.DEV])
    def test_it_is_used_at_penfields_own_addresses(self, tmp_path: Path, env: Environment) -> None:
        self._legacy(tmp_path)
        auth = PenfieldAuth(PenfieldConfig(env=env), tmp_path)
        assert auth.get_header() == "Bearer legacy-access"

    def test_it_is_not_sent_to_a_custom_address(self, api: FakePenfield, tmp_path: Path) -> None:
        self._legacy(tmp_path)
        auth = PenfieldAuth(_config(api), tmp_path)
        with pytest.raises(AuthError, match="Penfield is not connected"):
            auth.get_header()
        assert api.calls() == []

    def test_logging_out_at_a_custom_address_only_forgets_it(
        self, api: FakePenfield, tmp_path: Path
    ) -> None:
        self._legacy(tmp_path)
        assert PenfieldAuth(_config(api), tmp_path).logout() is False
        assert api.calls() == []
        assert not (tmp_path / "penfield" / "tokens.json").exists()
