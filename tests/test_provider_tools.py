# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Extra coverage: provider handle_tool_call + tools edge cases."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hermes_penfield.provider import PenfieldMemoryProvider


class _FakeClient:
    def call(self, *a: Any, **k: Any) -> Any:
        raise RuntimeError("should not be called directly by provider")


class TestHandleToolCall:
    def test_unknown_tool_returns_error_json(self, tmp_path: Any) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
        result = p.handle_tool_call("does_not_exist", {})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_not_initialized_raises(self) -> None:
        p = PenfieldMemoryProvider()
        with pytest.raises(RuntimeError, match="not initialized"):
            p.handle_tool_call("penfield_store", {"content": "x"})

    def test_store_dispatches_and_returns_id(
        self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))

        captured: dict[str, Any] = {}

        class StubClient:
            def call(self, name: str, *, body: Any = None, **kw: Any) -> Any:
                captured["name"] = name
                captured["body"] = body
                return {"id": "mem-1", "content": body["content"]}

        p._client = StubClient()  # type: ignore[assignment]
        result = p.handle_tool_call("penfield_store", {"content": "hello"})
        parsed = json.loads(result)
        assert parsed["id"] == "mem-1"
        assert captured["name"] == "memory_create"

    def test_validation_error_caught(self, tmp_path: Any) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))

        class StubClient:
            def call(self, *a: Any, **k: Any) -> Any:
                raise ValueError("bad input")

        p._client = StubClient()  # type: ignore[assignment]
        result = p.handle_tool_call("penfield_store", {"content": "x"})
        parsed = json.loads(result)
        assert "error" in parsed


class TestProviderLifecycle:
    def test_shutdown_and_session_end_no_op(self, tmp_path: Any) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
        # These must not raise.
        p.on_session_end()
        p.shutdown()

    def test_get_config_schema(self, tmp_path: Any) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
        schema = p.get_config_schema()
        keys = {c["key"] for c in schema}
        assert "auth_method" in keys
        assert "api_key" in keys

    def test_save_config_persists(self, tmp_path: Any) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
        p.save_config({"penfield_env": "dev"}, str(tmp_path))
        import pathlib

        cfg_file = pathlib.Path(str(tmp_path)) / "penfield" / "config.json"
        assert cfg_file.exists()
        saved = json.loads(cfg_file.read_text())
        assert saved["penfield_env"] == "dev"
        # api_key never persisted.
        assert "api_key" not in saved


class TestMirrorOptIn:
    def test_opt_in_mirrors(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        p = PenfieldMemoryProvider()
        p.initialize("s", hermes_home=str(tmp_path))
        p._config.mirror_builtin_writes = True  # type: ignore[union-attr]

        captured: dict[str, Any] = {}

        class StubClient:
            def call(self, name: str, *, body: Any = None, **kw: Any) -> Any:
                captured["body"] = body
                return {"id": "x"}

        p._client = StubClient()  # type: ignore[assignment]
        p.on_memory_write("append", "MEMORY.md", "a note")
        assert "MEMORY.md" in str(captured["body"]["tags"])


class TestToolsEdgeCases:
    def test_update_with_protected_type_rejected(self) -> None:
        from hermes_penfield.tools import dispatch

        class C:
            def call(self, *a: Any, **k: Any) -> Any:
                raise AssertionError("should not call")

        with pytest.raises(ValueError, match="protected"):
            dispatch(C(), "penfield_update", {"memory_id": "m", "memory_type": "identity_core"})

    def test_explore_passes_body(self) -> None:
        from hermes_penfield.tools import dispatch

        class C:
            def __init__(self) -> None:
                self.last: tuple[str, dict[str, Any]] = ("", {})

            def call(self, name: str, *, body: Any = None, **kw: Any) -> Any:
                self.last = (name, body or {})
                return {"paths": []}

        c = C()
        dispatch(c, "penfield_explore", {"start_memory_id": "abc", "max_depth": 2})
        assert c.last[0] == "relationship_traverse"
        assert c.last[1]["start_memory_id"] == "abc"

    def test_reflect_passes_window(self) -> None:
        from hermes_penfield.tools import dispatch

        class C:
            def __init__(self) -> None:
                self.last: tuple[str, dict[str, Any]] = ("", {})

            def call(self, name: str, *, body: Any = None, **kw: Any) -> Any:
                self.last = (name, body or {})
                return {"insights": []}

        c = C()
        dispatch(c, "penfield_reflect", {"time_window": "week"})
        assert c.last[0] == "analysis_reflect"
        assert c.last[1]["time_window"] == "week"
