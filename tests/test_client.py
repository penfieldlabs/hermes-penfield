# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the client: envelope unwrapping, backoff, rate limit.

Network is fully mocked via monkeypatching ``urllib.request.urlopen``.
"""

from __future__ import annotations

import http.client
import io
import json
import urllib.error
from typing import Any

import pytest
from penfield.client import PenfieldClient, _unwrap
from penfield.exceptions import APIError, NotFoundError, RateLimitError, ValidationError

# ---------------------------------------------------------------------------
# Fake response machinery
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)

    def __enter__(self) -> FakeResp:
        return self

    def __exit__(self, *_: object) -> None:
        self._buf.close()

    def read(self) -> bytes:
        return self._buf.getvalue()


def make_http_error(
    code: int, body: bytes, headers: dict[str, str] | None = None
) -> urllib.error.HTTPError:
    msg = http.client.HTTPMessage()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(
        url="https://example.com",
        code=code,
        msg="error",
        hdrs=msg,
        fp=io.BytesIO(body),
    )


# ---------------------------------------------------------------------------
# Envelope unwrapping
# ---------------------------------------------------------------------------


class TestUnwrap:
    def test_success_envelope_returns_data(self) -> None:
        raw = json.dumps({"status": "success", "data": {"id": "abc"}, "meta": {}}).encode()
        assert _unwrap(raw) == {"id": "abc"}

    def test_empty_body_returns_none(self) -> None:
        assert _unwrap(b"") is None

    def test_error_envelope_raises(self) -> None:
        raw = json.dumps({"status": "error", "error": {"code": "X", "message": "bad"}}).encode()
        with pytest.raises(APIError):
            _unwrap(raw)

    def test_non_envelope_passthrough(self) -> None:
        raw = json.dumps({"plain": True}).encode()
        assert _unwrap(raw) == {"plain": True}


# ---------------------------------------------------------------------------
# Request path resolution
# ---------------------------------------------------------------------------


class TestPathResolution:
    def test_endpoint_name_resolves(
        self, client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, str] = {}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            captured["url"] = req.full_url
            return FakeResp(b"")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        client.call("memory_get", path_params={"memory_id": "abc-123"})
        assert captured["url"].endswith("/memories/abc-123")

    def test_unknown_endpoint_raises(self, client: PenfieldClient) -> None:
        with pytest.raises(APIError):
            client.call("does_not_exist")


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------


class TestErrorClassification:
    def test_404_maps_to_not_found(
        self, client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = json.dumps({"error": {"code": "RES_NOT_FOUND", "message": "nope"}}).encode()

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            raise make_http_error(404, body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(NotFoundError):
            client.call("memory_get", path_params={"memory_id": "x"})

    def test_422_maps_to_validation(
        self, client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = json.dumps({"error": {"code": "VAL_VALIDATION_FAILED", "message": "bad"}}).encode()

        calls = {"n": 0}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            calls["n"] += 1
            raise make_http_error(422, body)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(ValidationError):
            client.call("memory_create", body={"content": "x"})
        assert calls["n"] == 1  # 422 is not retried


# ---------------------------------------------------------------------------
# Retry / backoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    def test_500_retries_then_succeeds(
        self, client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = json.dumps({"status": "success", "data": {"ok": True}}).encode()
        calls = {"n": 0}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            calls["n"] += 1
            if calls["n"] < 3:
                raise make_http_error(500, b"server error")
            return FakeResp(good)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = client.call("memory_get", path_params={"memory_id": "x"})
        assert result == {"ok": True}
        assert calls["n"] == 3

    def test_429_exhausts_and_raises(
        self, client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            raise make_http_error(429, b"slow down", headers={"Retry-After": "0"})

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(RateLimitError):
            client.call("memory_get", path_params={"memory_id": "x"})

    def test_401_triggers_refresh_then_retry(
        self, client: PenfieldClient, fake_auth: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good = json.dumps({"status": "success", "data": {"ok": True}}).encode()
        calls = {"n": 0}

        def fake_urlopen(req: urllib.request.Request, timeout: float = 0) -> FakeResp:
            calls["n"] += 1
            if calls["n"] == 1:
                raise make_http_error(401, b"unauth")
            return FakeResp(good)

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = client.call("memory_get", path_params={"memory_id": "x"})
        assert result == {"ok": True}
        assert fake_auth.refresh_calls == 1
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_blocks_when_window_full(
        self, dev_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []

        class Auth:
            def get_header(self) -> str:
                return "Bearer x"

            def refresh(self) -> None:
                pass

        c = PenfieldClient(Auth(), dev_config, max_rpm=2, sleep=lambda s: sleeps.append(s))
        good = json.dumps({"status": "success", "data": {}}).encode()
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp(good))
        c.call("memory_get", path_params={"memory_id": "a"})
        c.call("memory_get", path_params={"memory_id": "b"})
        # Third call should exceed the cap and trigger a sleep.
        c.call("memory_get", path_params={"memory_id": "c"})
        assert sleeps, "expected rate-limit sleep on 3rd call"
