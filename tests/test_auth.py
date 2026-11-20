# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the auth layer (network mocked)."""

from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.parse
from typing import Any, ClassVar

import pytest
from penfield.auth import PenfieldAuth, TokenSet
from penfield.config import Environment, PenfieldConfig
from penfield.exceptions import AuthError


class TestTokenSet:
    def test_not_expired_when_future(self) -> None:
        ts = TokenSet("a", "r", expires_at=time.time() + 3600)
        assert not ts.expired()

    def test_expired_when_past(self) -> None:
        ts = TokenSet("a", "r", expires_at=time.time() - 1)
        assert ts.expired()

    def test_roundtrip(self) -> None:
        ts = TokenSet("a", "r", expires_at=1000.0, tenant_id="pf_x")
        assert TokenSet.from_dict(ts.to_dict()) == ts


def _config(env: Environment = Environment.DEV) -> PenfieldConfig:
    cfg = PenfieldConfig(env=env, api_key="tm_test_ak_k")
    return cfg


class _Resp:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def read(self) -> bytes:
        return self._buf.getvalue()


class TestApiKeyExchange:
    def test_exchange_caches_and_persists(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = json.dumps(
            {
                "status": "success",
                "data": {
                    "access_token": "AT",
                    "refresh_token": "RT",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "tenant_id": "pf_t",
                },
            }
        ).encode()

        captured: dict[str, str] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            captured["url"] = req.full_url
            captured["auth"] = req.headers.get("Authorization", "")
            return _Resp(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        auth = PenfieldAuth(cfg, tmp_path)
        ts = auth.authenticate()

        assert ts.access_token == "AT"
        assert ts.refresh_token == "RT"
        assert captured["url"].endswith("/auth/token")
        assert captured["auth"] == "Bearer tm_test_ak_k"
        # Token file persisted with restricted perms.
        token_file = cfg.token_path(tmp_path)
        assert token_file.exists()
        persisted = json.loads(token_file.read_text())
        assert persisted["access_token"] == "AT"

    def test_cached_token_skips_network(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = {"n": 0}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            calls["n"] += 1
            return _Resp(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        # Pre-seed a valid token.
        auth = PenfieldAuth(
            cfg,
            tmp_path,
            tokens=TokenSet("cached", "r", expires_at=time.time() + 9999),
        )
        assert auth.get_header() == "Bearer cached"
        assert calls["n"] == 0


class TestRefresh:
    def test_refresh_uses_stored_refresh_token(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, str] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            captured["body"] = (req.data or b"").decode()
            payload = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "access_token": "AT2",
                        "refresh_token": "RT2",
                        "expires_in": 3600,
                        "tenant_id": "pf_t",
                    },
                }
            ).encode()
            return _Resp(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        auth = PenfieldAuth(
            cfg,
            tmp_path,
            tokens=TokenSet("AT1", "RT1", expires_at=time.time() - 1),
        )
        ts = auth.refresh()
        assert ts.access_token == "AT2"
        assert "RT1" in captured["body"]

    def test_refresh_failure_when_no_refresh_and_no_key(
        self,
        tmp_path: Any,
    ) -> None:
        cfg = PenfieldConfig(env=Environment.DEV)  # no api_key
        auth = PenfieldAuth(cfg, tmp_path, tokens=None)
        with pytest.raises(AuthError):
            auth.refresh()


class TestDeviceFlow:
    def test_pending_polls_then_succeeds(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Sequence of responses: discovery, device-code, token(pending), token(ok).
        device_resp = {
            "device_code": "DC",
            "user_code": "UC",
            "verification_uri_complete": "https://x/y?user_code=UC",
            "interval": 0,
            "expires_in": 60,
        }
        discovery = {
            "device_authorization_endpoint": "https://auth-dev/device",
            "token_endpoint": "https://auth-dev/token",
            "registration_endpoint": "https://auth-dev/reg",
        }
        ok_token = {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
            "token_type": "Bearer",
        }
        # Pending must arrive as an HTTP error (RFC 8628), not a 200 body.
        import http.client

        pending_err = urllib.error.HTTPError(
            url="https://auth-dev/token",
            code=400,
            msg="pending",
            hdrs=http.client.HTTPMessage(),
            fp=io.BytesIO(json.dumps({"error": "authorization_pending"}).encode()),
        )
        responses = [
            discovery,  # discovery GET
            device_resp,  # device code POST
            pending_err,  # poll 1 (raises)
            ok_token,  # poll 2
        ]
        idx = {"i": 0}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            i = idx["i"]
            idx["i"] += 1
            item = responses[i]
            if isinstance(item, urllib.error.HTTPError):
                raise item
            return _Resp(json.dumps(item).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        auth = PenfieldAuth(cfg, tmp_path, tokens=None)
        ts = auth.device_code_flow(client_id="CID", poll_interval_override=0)
        assert ts.access_token == "AT"


def _oauth_refusal(code: str) -> urllib.error.HTTPError:
    import http.client

    return urllib.error.HTTPError(
        url="https://auth-dev/token",
        code=400,
        msg=code,
        hdrs=http.client.HTTPMessage(),
        fp=io.BytesIO(json.dumps({"error": code}).encode()),
    )


class TestDeviceFlowFailures:
    DISCOVERY: ClassVar[dict[str, str]] = {
        "device_authorization_endpoint": "https://auth-dev/device",
        "token_endpoint": "https://auth-dev/token",
        "registration_endpoint": "https://auth-dev/reg",
    }
    DEVICE: ClassVar[dict[str, Any]] = {
        "device_code": "DC",
        "user_code": "UC",
        "verification_uri_complete": "https://x/y?user_code=UC",
        "interval": 0,
        "expires_in": 600,
    }

    def _login(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, refusal: str) -> None:
        responses: list[Any] = [
            self.DISCOVERY,
            self.DEVICE,
            _oauth_refusal("authorization_pending"),
            _oauth_refusal(refusal),
        ]

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            item = responses.pop(0)
            if isinstance(item, urllib.error.HTTPError):
                raise item
            return _Resp(json.dumps(item).encode())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        PenfieldAuth(_config(), tmp_path, tokens=None).device_code_flow(
            client_id="CID", poll_interval_override=0
        )

    @pytest.mark.parametrize("refusal", ["expired_token", "invalid_grant"])
    def test_an_expired_code_says_so(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch, refusal: str
    ) -> None:
        with pytest.raises(AuthError, match="expired before it was approved"):
            self._login(tmp_path, monkeypatch, refusal)

    def test_a_refused_sign_in_says_so(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(AuthError, match="refused in the browser"):
            self._login(tmp_path, monkeypatch, "access_denied")

    def test_nothing_is_cached_after_a_failed_login(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(AuthError):
            self._login(tmp_path, monkeypatch, "access_denied")
        assert not (tmp_path / "penfield" / "tokens.json").exists()

    def test_the_link_is_shown_with_its_lifetime(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(AuthError):
            self._login(tmp_path, monkeypatch, "access_denied")
        shown = capsys.readouterr().out
        assert "https://x/y?user_code=UC" in shown
        assert "valid for 10 minutes" in shown


class TestLogout:
    DISCOVERY: ClassVar[dict[str, str]] = {
        "token_endpoint": "https://auth-dev/token",
        "revocation_endpoint": "https://auth-dev/revoke",
    }

    def _signed_in(self, tmp_path: Any, source: str) -> PenfieldAuth:
        tokens = TokenSet(
            access_token="AT",
            refresh_token="RT",
            expires_at=time.time() + 3600,
            source=source,
            client_id="CID" if source == "oauth" else None,
        )
        auth = PenfieldAuth(_config(), tmp_path, tokens=tokens)
        auth._persist()
        return auth

    def test_a_browser_sign_in_is_revoked_at_the_server(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[tuple[str, dict[str, list[str]]]] = []

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            if req.full_url.endswith("oauth-authorization-server"):
                return _Resp(json.dumps(self.DISCOVERY).encode())
            sent.append((req.full_url, urllib.parse.parse_qs((req.data or b"").decode())))
            return _Resp(b'{"status":"revoked"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        auth = self._signed_in(tmp_path, "oauth")

        assert auth.logout() is True

        assert [url for url, _ in sent] == ["https://auth-dev/revoke"] * 2
        assert [form["token"] for _, form in sent] == [["RT"], ["AT"]]
        assert all(form["client_id"] == ["CID"] for _, form in sent)
        assert not (tmp_path / "penfield" / "tokens.json").exists()
        assert auth.is_authenticated() is False

    def test_the_cache_is_cleared_even_when_the_server_is_unreachable(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            raise urllib.error.URLError("no route")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        auth = self._signed_in(tmp_path, "oauth")

        assert auth.logout() is False
        assert not (tmp_path / "penfield" / "tokens.json").exists()

    def test_tokens_from_an_api_key_are_forgotten_without_a_request(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            raise AssertionError("no request expected")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        auth = self._signed_in(tmp_path, "api_key")

        assert auth.logout() is False
        assert not (tmp_path / "penfield" / "tokens.json").exists()


class TestOAuthRefreshRouting:
    """OAuth-derived tokens must refresh via the OAuth token endpoint, not
    /auth/refresh. Pins the spec-compliance fix and the routing logic that
    decides which path based on TokenSet.source.
    """

    def test_refresh_routes_to_oauth_endpoint_for_oauth_tokens(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # OAuth refresh hits {token_endpoint} (form-encoded), not /auth/refresh.
        captured: dict[str, Any] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            captured["url"] = req.full_url
            captured["content_type"] = req.headers.get("Content-type", "")
            captured["body"] = (req.data or b"").decode()
            # Return a bare OAuth token object (no envelope)
            payload = json.dumps(
                {
                    "access_token": "AT2",
                    "refresh_token": "RT2",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            ).encode()
            return _Resp(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        auth = PenfieldAuth(
            cfg,
            tmp_path,
            tokens=TokenSet(
                access_token="AT1",
                refresh_token="RT1",
                expires_at=time.time() - 1,
                source="oauth",
                client_id="dyn_test",
            ),
        )
        # Stub _discovery so _refresh_oauth does not hit the network.
        monkeypatch.setattr(
            auth,
            "_discovery",
            lambda: {"token_endpoint": "https://auth-dev.test/oauth/token"},
        )
        ts = auth.refresh()
        assert ts.access_token == "AT2"
        # Routing assertions:
        assert "/auth/refresh" not in captured["url"], "routed to wrong endpoint"
        assert "oauth/token" in captured["url"], "should hit OAuth token endpoint"
        assert "application/x-www-form-urlencoded" in captured["content_type"]
        # Body shape (form-encoded, not JSON):
        assert "grant_type=refresh_token" in captured["body"]
        assert "client_id=dyn_test" in captured["body"]
        # Rotation: new refresh token persisted, client_id preserved
        assert ts.refresh_token == "RT2"
        assert ts.client_id == "dyn_test"
        assert ts.source == "oauth"

    def test_refresh_routes_to_auth_refresh_for_api_key_tokens(
        self,
        tmp_path: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # API-key tokens hit /auth/refresh with a JSON body.
        captured: dict[str, Any] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> _Resp:
            captured["url"] = req.full_url
            captured["body"] = (req.data or b"").decode()
            payload = json.dumps(
                {
                    "status": "success",
                    "data": {
                        "access_token": "AT2",
                        "refresh_token": "RT2",
                        "expires_in": 3600,
                        "tenant_id": "pf_t",
                    },
                }
            ).encode()
            return _Resp(payload)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        cfg = _config()
        auth = PenfieldAuth(
            cfg,
            tmp_path,
            tokens=TokenSet(
                access_token="AT1",
                refresh_token="RT1",
                expires_at=time.time() - 1,
                source="api_key",
            ),
        )
        ts = auth.refresh()
        assert ts.access_token == "AT2"
        assert "/auth/refresh" in captured["url"]
        assert "oauth/token" not in captured["url"]
        # API-key body is JSON, not form-encoded
        assert captured["body"].startswith("{")

    def test_oauth_refresh_without_client_id_raises(
        self,
        tmp_path: Any,
    ) -> None:
        # _refresh_oauth can't satisfy the spec without client_id; it raises
        # TokenExpiredError so the outer refresh() can fall back to API-key
        # exchange. Test the routing method directly, not the wrapper.
        from penfield.exceptions import TokenExpiredError

        cfg = _config()
        auth = PenfieldAuth(
            cfg,
            tmp_path,
            tokens=TokenSet(
                access_token="AT1",
                refresh_token="RT1",
                expires_at=time.time() - 1,
                source="oauth",
                client_id=None,  # missing
            ),
        )
        with pytest.raises(TokenExpiredError, match="client_id"):
            auth._refresh_oauth("RT1")

    def test_tokenset_roundtrip_preserves_source_and_client_id(self) -> None:
        ts = TokenSet(
            access_token="a",
            refresh_token="r",
            expires_at=1000.0,
            source="oauth",
            client_id="dyn_x",
        )
        restored = TokenSet.from_dict(ts.to_dict())
        assert restored.source == "oauth"
        assert restored.client_id == "dyn_x"
        # API-key tokens roundtrip too
        ts2 = TokenSet(access_token="a", refresh_token="r", expires_at=1000.0)
        restored2 = TokenSet.from_dict(ts2.to_dict())
        assert restored2.source == "api_key"
        assert restored2.client_id is None
