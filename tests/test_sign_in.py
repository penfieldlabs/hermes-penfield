# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Browser sign-in against the fake auth server, over real HTTP.

Covers the life of a sign-in: getting one, renewing it, what happens when
the auth server is unwell, and how a login that cannot be renewed is told
apart from one that only could not be renewed right now.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from penfield.auth import PenfieldAuth
from penfield.client import PenfieldClient
from penfield.config import Environment, PenfieldConfig
from penfield.constants import SIGN_IN_SCOPE
from penfield.exceptions import AuthError, AuthTransportError, PenfieldError, TokenExpiredError

from tests.support.fake_penfield import FakePenfield

KEY = "tm_pf_test_ak_unit"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the wait for another process to rotate a token; no other process is here."""
    monkeypatch.setattr("penfield.auth._ROTATION_WAIT_SECONDS", 0.05)


def _config(api: FakePenfield, *, api_key: str | None = None) -> PenfieldConfig:
    cfg = PenfieldConfig(env=Environment.DEV, api_key=api_key)
    cfg.api_base = api.url
    return cfg


def _cache_path(home: Path) -> Path:
    return home / "penfield" / "tokens.json"


def _saved(home: Path) -> dict[str, object]:
    saved: dict[str, object] = json.loads(_cache_path(home).read_text(encoding="utf-8"))
    return saved


def _save_sign_in(
    api: FakePenfield, home: Path, *, expires_in: float = 3600.0, scope: str = SIGN_IN_SCOPE
) -> None:
    """Leave a finished browser sign-in in the profile, as a login elsewhere would."""
    issued = api.sign_in(scope)
    path = _cache_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "access_token": issued["access_token"],
                "refresh_token": issued.get("refresh_token", ""),
                "expires_at": time.time() + expires_in,
                "source": "oauth",
                "client_id": issued["client_id"],
                "api_base": api.url,
            }
        ),
        encoding="utf-8",
    )


def _signed_in(
    api: FakePenfield, home: Path, *, expires_in: float = 3600.0, scope: str = SIGN_IN_SCOPE
) -> PenfieldClient:
    """A client whose profile holds a finished browser sign-in."""
    _save_sign_in(api, home, expires_in=expires_in, scope=scope)
    cfg = _config(api)
    return PenfieldClient(PenfieldAuth(cfg, home), cfg, sleep=lambda _s: None)


# ---------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------


def test_login_signs_in_and_saves_the_tokens(
    api: FakePenfield, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api.approve_sign_ins = True
    auth = PenfieldAuth(_config(api), tmp_path)

    auth.device_code_flow()

    shown = capsys.readouterr().out
    assert f"{api.root}/device?user_code=TEST-0001" in shown
    saved = _saved(tmp_path)
    assert saved["source"] == "oauth"
    assert saved["client_id"] == "client-1"
    cfg = _config(api)
    client = PenfieldClient(PenfieldAuth(cfg, tmp_path), cfg, sleep=lambda _s: None)
    assert client.call("search_stats")["total_memories"] == 0


def test_login_asks_for_everything_the_tools_need(api: FakePenfield, tmp_path: Path) -> None:
    """Two of the tools delete; a sign-in without that permission cannot run them."""
    api.approve_sign_ins = True

    PenfieldAuth(_config(api), tmp_path).device_code_flow()

    asked = api.calls("POST", "/oauth/device_authorization")[0].body
    assert "scope=read+write+delete+offline_access" in asked
    assert _saved(tmp_path)["scope"] == "read write delete offline_access"


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_login_keeps_waiting_when_the_auth_server_stumbles(
    api: FakePenfield, tmp_path: Path, status: int
) -> None:
    """One bad answer while waiting for the approval must not end the login."""
    api.approve_sign_ins = True
    api.fail_next[("POST", "/oauth/token")] = [status, status]

    PenfieldAuth(_config(api), tmp_path).device_code_flow()

    assert _saved(tmp_path)["source"] == "oauth"
    assert len(api.calls("POST", "/oauth/token")) == 3


def test_a_refused_login_saves_nothing(api: FakePenfield, tmp_path: Path) -> None:
    api.refuse_sign_ins = True

    with pytest.raises(AuthError, match="refused in the browser"):
        PenfieldAuth(_config(api), tmp_path).device_code_flow()

    assert not _cache_path(tmp_path).exists()


def test_login_says_when_the_auth_server_keeps_failing(api: FakePenfield, tmp_path: Path) -> None:
    """Someone who has approved in the browser must not be left guessing."""
    api.approve_sign_ins = True
    api.fail_next[("POST", "/oauth/token")] = [503] * 5
    said: list[str] = []

    PenfieldAuth(_config(api), tmp_path).device_code_flow(say=said.append)

    assert [line for line in said if "not answering" in line] == [
        "The sign-in server is not answering: auth server error (503). Still trying."
    ]
    assert _saved(tmp_path)["source"] == "oauth"


def test_login_says_nothing_about_a_stumble(api: FakePenfield, tmp_path: Path) -> None:
    api.approve_sign_ins = True
    api.fail_next[("POST", "/oauth/token")] = [503, 503]
    said: list[str] = []

    PenfieldAuth(_config(api), tmp_path).device_code_flow(say=said.append)

    assert not [line for line in said if "not answering" in line]


def test_signing_in_again_ends_the_sign_in_it_replaces(api: FakePenfield, tmp_path: Path) -> None:
    """Once overwritten, the earlier sign-in is out of reach of ``logout``."""
    _save_sign_in(api, tmp_path)
    before = _saved(tmp_path)
    api.approve_sign_ins = True

    PenfieldAuth(_config(api), tmp_path).device_code_flow(say=lambda _line: None)

    after = _saved(tmp_path)
    assert after["access_token"] != before["access_token"]
    assert api.revoked == [before["refresh_token"], before["access_token"]]
    assert after["refresh_token"] in api.valid_refresh_tokens


def test_a_first_sign_in_ends_nothing(api: FakePenfield, tmp_path: Path) -> None:
    api.approve_sign_ins = True

    PenfieldAuth(_config(api), tmp_path).device_code_flow(say=lambda _line: None)

    assert api.revoked == []
    assert api.calls("POST", "/oauth/revoke") == []


def test_a_sign_in_that_does_not_finish_leaves_the_earlier_one_alone(
    api: FakePenfield, tmp_path: Path
) -> None:
    client = _signed_in(api, tmp_path)
    api.refuse_sign_ins = True

    with pytest.raises(AuthError, match="refused in the browser"):
        PenfieldAuth(_config(api), tmp_path).device_code_flow(say=lambda _line: None)

    assert api.revoked == []
    assert client.call("search_stats")["total_memories"] == 0


def test_a_new_sign_in_is_kept_when_the_earlier_one_cannot_be_ended(
    api: FakePenfield, tmp_path: Path
) -> None:
    _save_sign_in(api, tmp_path)
    before = _saved(tmp_path)
    api.approve_sign_ins = True
    api.fail_next[("POST", "/oauth/revoke")] = [503]

    PenfieldAuth(_config(api), tmp_path).device_code_flow(say=lambda _line: None)

    assert _saved(tmp_path)["access_token"] != before["access_token"]
    cfg = _config(api)
    client = PenfieldClient(PenfieldAuth(cfg, tmp_path), cfg, sleep=lambda _s: None)
    assert client.call("search_stats")["total_memories"] == 0


# ---------------------------------------------------------------------------
# Renewal
# ---------------------------------------------------------------------------


def test_a_sign_in_renews_itself_when_its_access_token_runs_out(
    api: FakePenfield, tmp_path: Path
) -> None:
    client = _signed_in(api, tmp_path, expires_in=-60)
    before = _saved(tmp_path)

    assert client.call("search_stats")["total_memories"] == 0

    after = _saved(tmp_path)
    assert after["access_token"] != before["access_token"]
    assert after["refresh_token"] != before["refresh_token"]
    assert after["client_id"] == before["client_id"]
    assert after["scope"] == SIGN_IN_SCOPE


@pytest.mark.parametrize("status", [500, 502, 503, 429])
def test_one_bad_answer_from_the_auth_server_does_not_end_a_sign_in(
    api: FakePenfield, tmp_path: Path, status: int
) -> None:
    """The renewal is tried again; the sign-in itself is still good."""
    client = _signed_in(api, tmp_path, expires_in=-60)
    api.fail_next[("POST", "/oauth/token")] = [status]

    assert client.call("search_stats")["total_memories"] == 0
    assert len(api.calls("POST", "/oauth/token")) == 2


def test_a_sign_in_survives_an_auth_server_outage(api: FakePenfield, tmp_path: Path) -> None:
    """While the auth server is down requests fail; once it is back they work.

    Nothing is forgotten in between, in memory or on disk, so a long-running
    gateway needs no restart and no new login.
    """
    client = _signed_in(api, tmp_path, expires_in=-60)
    before = _saved(tmp_path)
    api.fail_next[("POST", "/oauth/token")] = [503] * 6

    for _ in range(2):
        with pytest.raises(AuthTransportError):
            client.call("search_stats")
    assert _saved(tmp_path) == before

    api.fail_next.clear()
    assert client.call("search_stats")["total_memories"] == 0


def test_a_sign_in_the_server_no_longer_accepts_says_to_sign_in_again(
    api: FakePenfield, tmp_path: Path
) -> None:
    client = _signed_in(api, tmp_path, expires_in=-60)
    api.valid_refresh_tokens.clear()  # revoked in the portal, or run out

    with pytest.raises(TokenExpiredError, match="hermes memory setup penfield") as caught:
        client.call("search_stats")

    assert "no longer valid" in str(caught.value)
    # Asked again, the answer is the same and no further request is made.
    renewals = len(api.calls("POST", "/oauth/token"))
    with pytest.raises(TokenExpiredError, match="no longer valid"):
        client.call("search_stats")
    assert len(api.calls("POST", "/oauth/token")) == renewals


@pytest.mark.parametrize("expires_in", [3600.0, -60.0], ids=["at once", "a day later"])
def test_a_new_sign_in_replaces_one_that_ended(
    api: FakePenfield, tmp_path: Path, expires_in: float
) -> None:
    """Also for a process that sat idle until the new access token ran out."""
    client = _signed_in(api, tmp_path, expires_in=-60)
    api.valid_refresh_tokens.clear()
    with pytest.raises(TokenExpiredError):
        client.call("search_stats")

    # The user signs in again. This process looks when its next request comes.
    _save_sign_in(api, tmp_path, expires_in=expires_in)

    assert client.call("search_stats")["total_memories"] == 0


def test_a_refresh_token_another_process_has_used_is_not_sent_again(
    api: FakePenfield, tmp_path: Path
) -> None:
    """A server that revokes a sign-in on reuse would end it for both."""
    client = _signed_in(api, tmp_path, expires_in=-60)
    used = _saved(tmp_path)["refresh_token"]
    PenfieldAuth(_config(api), tmp_path).authenticate()  # another process renews
    saved = _saved(tmp_path)
    saved["expires_at"] = time.time() - 60  # both idle until that token runs out too
    _cache_path(tmp_path).write_text(json.dumps(saved), encoding="utf-8")
    api.reset_calls()

    assert client.call("search_stats")["total_memories"] == 0

    renewals = [str(call.body) for call in api.calls("POST", "/oauth/token")]
    assert len(renewals) == 1
    assert f"refresh_token={used}&" not in renewals[0]


def test_a_process_that_could_not_save_its_renewal_keeps_renewing(
    api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache then holds an older login than this process; it is not taken back."""
    client = _signed_in(api, tmp_path, expires_in=-60)
    auth = client._auth
    monkeypatch.setattr(PenfieldAuth, "_persist", lambda _self: None)  # a profile gone read-only
    assert client.call("search_stats")["total_memories"] == 0  # renews, cannot save
    assert auth._tokens is not None
    auth._tokens.expires_at = time.time() - 30  # a day later: newer than the cache, run out

    assert client.call("search_stats")["total_memories"] == 0


def test_a_sign_in_without_a_refresh_token_ends_when_its_access_token_does(
    api: FakePenfield, tmp_path: Path
) -> None:
    """Without ``offline_access`` the auth server hands out no refresh token."""
    api.approve_sign_ins = True
    auth = PenfieldAuth(_config(api), tmp_path)

    auth.device_code_flow(scope="read write")

    assert _saved(tmp_path)["refresh_token"] == ""
    cfg = _config(api)
    client = PenfieldClient(PenfieldAuth(cfg, tmp_path), cfg, sleep=lambda _s: None)
    assert client.call("search_stats")["total_memories"] == 0
    saved = _saved(tmp_path)
    saved["expires_at"] = time.time() - 60
    _cache_path(tmp_path).write_text(json.dumps(saved), encoding="utf-8")
    again = PenfieldClient(PenfieldAuth(cfg, tmp_path), cfg, sleep=lambda _s: None)
    with pytest.raises(TokenExpiredError, match="hermes memory setup penfield"):
        again.call("search_stats")


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------


def test_a_key_exchange_without_a_refresh_token_is_renewed_with_the_key(
    api: FakePenfield, tmp_path: Path
) -> None:
    """The API may answer a key exchange with ``"refresh_token": null``."""
    api.key_exchange_gives_refresh_token = False
    cfg = _config(api, api_key=KEY)
    auth = PenfieldAuth(cfg, tmp_path)
    client = PenfieldClient(auth, cfg, sleep=lambda _s: None)

    assert client.call("search_stats")["total_memories"] == 0
    assert _saved(tmp_path)["refresh_token"] == ""

    assert auth._tokens is not None
    auth._tokens.expires_at = time.time() - 60
    assert client.call("search_stats")["total_memories"] == 0

    assert api.calls("POST", "/auth/refresh") == []
    assert len(api.calls("POST", "/auth/token")) == 2


def test_logout_revokes_the_sign_in_at_the_server(api: FakePenfield, tmp_path: Path) -> None:
    client = _signed_in(api, tmp_path)
    saved = _saved(tmp_path)

    assert PenfieldAuth(_config(api), tmp_path).logout() is True

    assert api.revoked == [saved["refresh_token"], saved["access_token"]]
    assert not _cache_path(tmp_path).exists()
    with pytest.raises(PenfieldError):
        client.call("search_stats")
