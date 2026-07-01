# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the provider adapter layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest
from penfield.provider import PenfieldMemoryProvider


class _FakeClient:
    def __init__(self, result: Any = None) -> None:
        self.result = result if result is not None else {"items": []}
        self.last_dispatch: tuple[str, dict[str, Any]] | None = None


def _make_provider(tmp_path: Any) -> tuple[PenfieldMemoryProvider, _FakeClient]:
    p = PenfieldMemoryProvider()
    p.initialize("sess-1", hermes_home=str(tmp_path))
    fake = _FakeClient()
    p._client = fake  # type: ignore[assignment]
    return p, fake


class TestIdentity:
    def test_name(self) -> None:
        assert PenfieldMemoryProvider().name == "penfield"

    def test_tool_schemas_count(self, tmp_path: Any) -> None:
        p, _ = _make_provider(tmp_path)
        assert len(p.get_tool_schemas()) == 17


class TestSystemPrompt:
    def test_block_mentions_tools(self, tmp_path: Any) -> None:
        p, _ = _make_provider(tmp_path)
        block = p.system_prompt_block()
        assert "penfield_store" in block
        assert "penfield_recall" in block


class TestPrefetch:
    def test_empty_when_no_results(self, tmp_path: Any) -> None:
        p, fake = _make_provider(tmp_path)
        fake.result = {"items": []}
        # prefetch calls dispatch which uses the real client; replace dispatch path
        # by stubbing the module-level dispatch the provider imports.
        import penfield.provider as prov

        orig = prov.dispatch
        prov.dispatch = lambda c, n, a: json.dumps(fake.result)  # type: ignore[assignment]
        try:
            assert p.prefetch("hello") == ""
        finally:
            prov.dispatch = orig  # type: ignore[assignment]

    def test_returns_recalled_lines(self, tmp_path: Any) -> None:
        p, fake = _make_provider(tmp_path)
        fake.result = {
            "items": [
                {"id": "abcdefgh-1234", "snippet": "a fact", "score": 0.9},
            ]
        }
        import penfield.provider as prov

        orig = prov.dispatch
        prov.dispatch = lambda c, n, a: json.dumps(fake.result)  # type: ignore[assignment]
        try:
            out = p.prefetch("hello")
            assert "[Penfield memory" in out
            assert "abcdefgh" in out
        finally:
            prov.dispatch = orig  # type: ignore[assignment]


class TestPreCompress:
    def test_stores_checkpoint(self, tmp_path: Any) -> None:
        p, _fake = _make_provider(tmp_path)
        messages = [
            {"role": "user", "content": "do a thing"},
            {"role": "assistant", "content": "doing it"},
        ]
        import penfield.provider as prov

        captured: dict[str, Any] = {}

        def fake_dispatch(c: Any, name: str, args: dict[str, Any]) -> str:
            captured["name"] = name
            captured["args"] = args
            return "{}"

        orig = prov.dispatch
        prov.dispatch = fake_dispatch  # type: ignore[assignment]
        try:
            p.on_pre_compress(messages)
        finally:
            prov.dispatch = orig  # type: ignore[assignment]
        assert captured["name"] == "penfield_save_context"
        # Description is LLM-generated (or fallback). Just verify save_context was called.
        assert captured["args"]["name"].startswith("pre-compress-")

    def test_disabled_is_noop(self, tmp_path: Any) -> None:
        p, _fake = _make_provider(tmp_path)
        p._config.pre_compress_save = False  # type: ignore[union-attr]
        import penfield.provider as prov

        called = {"n": 0}

        def fake_dispatch(c: Any, name: str, args: dict[str, Any]) -> str:
            called["n"] += 1
            return "{}"

        orig = prov.dispatch
        prov.dispatch = fake_dispatch  # type: ignore[assignment]
        try:
            p.on_pre_compress([{"role": "user", "content": "x"}])
        finally:
            prov.dispatch = orig  # type: ignore[assignment]
        assert called["n"] == 0


class TestMemoryMirror:
    def test_off_by_default(self, tmp_path: Any) -> None:
        p, _ = _make_provider(tmp_path)
        import penfield.provider as prov

        called = {"n": 0}

        def fake_dispatch(c: Any, name: str, args: dict[str, Any]) -> str:
            called["n"] += 1
            return "{}"

        orig = prov.dispatch
        prov.dispatch = fake_dispatch  # type: ignore[assignment]
        try:
            p.on_memory_write("append", "MEMORY.md", "some note")
        finally:
            prov.dispatch = orig  # type: ignore[assignment]
        assert called["n"] == 0


class TestSyncTurn:
    def test_is_noop_and_does_not_raise(self, tmp_path: Any) -> None:
        p, _ = _make_provider(tmp_path)
        # Must not raise even though there is no /turns/sync endpoint.
        p.sync_turn("u", "a")


class TestAvailability:
    def test_no_creds_unavailable(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        p = PenfieldMemoryProvider()
        p._hermes_home = str(tmp_path)  # type: ignore[assignment]
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # No tokens file, no env key.
        assert p.is_available() is False
