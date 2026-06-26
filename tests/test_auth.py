# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the auth layer (network mocked)."""

from __future__ import annotations

import io
import json
import time
import urllib.error
from typing import Any

import pytest

from hermes_penfield.auth import PenfieldAuth, TokenSet
from hermes_penfield.config import Environment, PenfieldConfig
from hermes_penfield.exceptions import AuthError


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
