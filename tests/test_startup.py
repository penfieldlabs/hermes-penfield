# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Starting a session with settings or files the plugin cannot use.

Hermes keeps a memory provider whose ``initialize`` fails, tools and all,
so a failure has to be visible where people look: in what the agent is
told, in what a tool call answers and in the status commands.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from penfield.provider import TOOL_GUIDANCE, PenfieldMemoryProvider

from tests.support.fake_penfield import FakePenfield

KEY = "tm_pf_test_ak_unit"


def _start(home: Path) -> PenfieldMemoryProvider:
    provider = PenfieldMemoryProvider()
    provider.initialize("unit-session", hermes_home=str(home), platform="cli")
    return provider


@pytest.fixture
def connected(api: FakePenfield, monkeypatch: pytest.MonkeyPatch) -> FakePenfield:
    monkeypatch.setenv("PENFIELD_URL", api.url)
    monkeypatch.setenv("PENFIELD_API_KEY", KEY)
    return api


UNUSABLE = {
    "an unknown environment": ("PENFIELD_ENV", "staging", "unknown PENFIELD_ENV 'staging'"),
    "an address that is not https": ("PENFIELD_URL", "http://api.example.com/api/v2", "https"),
}


@pytest.mark.parametrize("case", list(UNUSABLE))
class TestASettingPenfieldCannotUse:
    @pytest.fixture
    def provider(
        self, case: str, connected: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> PenfieldMemoryProvider:
        name, value, _reason = UNUSABLE[case]
        monkeypatch.setenv(name, value)
        return _start(tmp_path)

    def test_the_session_starts_and_the_log_says_why_penfield_is_off(
        self,
        case: str,
        connected: FakePenfield,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        name, value, reason = UNUSABLE[case]
        monkeypatch.setenv(name, value)
        with caplog.at_level(logging.WARNING):
            _start(tmp_path)
        assert any(
            "Penfield is off" in r.getMessage() and reason in r.getMessage()
            for r in caplog.records
        )

    def test_the_agent_is_told_that_penfield_is_off_and_why(
        self, case: str, provider: PenfieldMemoryProvider
    ) -> None:
        block = provider.system_prompt_block()
        assert "Penfield memory is off" in block
        assert UNUSABLE[case][2] in block
        assert TOOL_GUIDANCE not in block

    def test_a_tool_call_says_why(self, case: str, provider: PenfieldMemoryProvider) -> None:
        answer = json.loads(provider.handle_tool_call("penfield_recall", {"query": "anything"}))
        assert UNUSABLE[case][2] in answer["error"]

    def test_nothing_else_fails(self, case: str, provider: PenfieldMemoryProvider) -> None:
        assert provider.prefetch("what did we decide about the launch") == ""
        assert provider.recall_status() is None
        provider.on_memory_write("add", "memory", "a note")
        assert provider.on_pre_compress([{"role": "user", "content": "a long talk"}]) == ""
        provider.on_session_end([])
        provider.shutdown()

    def test_status_names_the_problem(self, case: str, provider: PenfieldMemoryProvider) -> None:
        status = provider.get_status_config()
        assert UNUSABLE[case][2] in status["problem"]


DAMAGED_SETTINGS = {
    "a list": b"[]",
    "null": b"null",
    "not text": b"\xff\xfe\x00",
    "half a file": b'{"recall_limit": 8,',
    "a null environment": b'{"penfield_env": null, "recall_limit": 8}',
}


@pytest.mark.parametrize("stray", ["\n", "\r\n", "  ", "\t"])
def test_whitespace_around_the_api_key_is_dropped(
    connected: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stray: str
) -> None:
    """A key that comes from a file often ends with a line break."""
    monkeypatch.setenv("PENFIELD_API_KEY", f"{stray}{KEY}{stray}")
    provider = _start(tmp_path)

    answer = json.loads(provider.handle_tool_call("penfield_recall", {"query": "x"}))

    assert "error" not in answer
    assert connected.calls("POST", "/auth/token")[0].authorization == f"Bearer {KEY}"


def test_a_damaged_api_key_is_named_to_the_agent_without_showing_it(
    connected: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test\u200b_ak_SECRETPART")
    provider = _start(tmp_path)

    answer = json.loads(provider.handle_tool_call("penfield_recall", {"query": "x"}))

    assert answer["error"] == (
        "the API key cannot be used: it contains the character ZERO WIDTH SPACE "
        "at position 11, which cannot be part of a key"
    )
    assert connected.calls() == []


@pytest.mark.parametrize("content", list(DAMAGED_SETTINGS))
def test_a_damaged_settings_file_does_not_stop_penfield(
    content: str, connected: FakePenfield, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "penfield").mkdir()
    (tmp_path / "penfield" / "config.json").write_bytes(DAMAGED_SETTINGS[content])

    with caplog.at_level(logging.WARNING):
        provider = _start(tmp_path)

    assert TOOL_GUIDANCE in provider.system_prompt_block()
    answer = json.loads(provider.handle_tool_call("penfield_recall", {"query": "anything"}))
    assert answer == {"items": [], "count": 0}
    if content != "a null environment":
        assert any("ignoring penfield/config.json" in r.getMessage() for r in caplog.records)
    provider.shutdown()


@pytest.mark.parametrize("content", [b"[1]", b"null", b'"text"', b"\xff\xfe\x00", b"{}"])
def test_a_damaged_token_cache_is_the_same_as_none(
    content: bytes, connected: FakePenfield, tmp_path: Path
) -> None:
    (tmp_path / "penfield").mkdir()
    (tmp_path / "penfield" / "tokens.json").write_bytes(content)

    provider = _start(tmp_path)

    answer = json.loads(provider.handle_tool_call("penfield_recall", {"query": "anything"}))
    assert answer == {"items": [], "count": 0}
    assert provider.is_available() is True
    provider.shutdown()


def test_recalled_text_ends_with_a_whole_memory(connected: FakePenfield, tmp_path: Path) -> None:
    """The size limit drops whole memories; it never cuts one in the middle."""
    for n in range(5):
        connected.seed_memory(f"launch checklist item {n}: " + "detail " * 150 + f"END{n}")
    provider = _start(tmp_path)
    assert provider._config is not None
    limit = provider._config.max_prefetch_chars

    text = provider.prefetch("launch checklist item detail")

    lines = text.splitlines()
    assert lines[0] == "[Penfield memory: recalled context]"
    memories = lines[1:]
    assert 0 < len(memories) < 5
    assert len(text) <= limit
    assert all(line.endswith("(penfield_fetch this id for the rest)") for line in memories)
    status = provider.recall_status()
    assert status is None or status.count == len(memories)
    assert provider._recalled == len(memories)
    provider.shutdown()


def test_one_memory_larger_than_the_limit_is_still_recalled(
    connected: FakePenfield, tmp_path: Path
) -> None:
    connected.seed_memory("launch checklist: " + "detail " * 150)
    (tmp_path / "penfield").mkdir()
    (tmp_path / "penfield" / "config.json").write_text('{"max_prefetch_chars": 200}')
    provider = _start(tmp_path)

    text = provider.prefetch("launch checklist detail")

    assert provider._recalled == 1
    assert len(text) <= 200
    assert text.endswith("(penfield_fetch this id for the rest)")
    provider.shutdown()


# ---------------------------------------------------------------------------
# A login that may not do everything
# ---------------------------------------------------------------------------


class TestAReadOnlyApiKey:
    @pytest.fixture
    def provider(self, connected: FakePenfield, tmp_path: Path) -> PenfieldMemoryProvider:
        connected.api_key_scopes[KEY] = frozenset({"read"})
        connected.seed_memory("the launch is on the 14th")
        return _start(tmp_path)

    def test_reads_work(self, provider: PenfieldMemoryProvider) -> None:
        assert "=== AWAKENING BRIEFING ===" in provider.system_prompt_block()
        assert "the launch is on the 14th" in provider.prefetch("when is the launch")
        found = json.loads(provider.handle_tool_call("penfield_recall", {"query": "launch"}))
        assert found["count"] == 1
        reflected = json.loads(provider.handle_tool_call("penfield_reflect", {}))
        assert "error" not in reflected

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("penfield_store", {"content": "a new note"}),
            ("penfield_save_artifact", {"path": "/notes/a.md", "content": "x"}),
            ("penfield_save_context", {"name": "handoff", "description": "launch planning notes"}),
            ("penfield_delete_artifact", {"path": "/notes/a.md"}),
        ],
    )
    def test_a_write_is_refused_and_the_agent_is_told_what_the_login_may_do(
        self, provider: PenfieldMemoryProvider, tool: str, args: dict[str, str]
    ) -> None:
        answer = json.loads(provider.handle_tool_call(tool, args))
        assert answer["error"].startswith("Penfield refused this:")
        assert answer["error"].endswith("This Penfield login may read only.")

    def test_status_says_what_the_key_may_do(self, provider: PenfieldMemoryProvider) -> None:
        provider.system_prompt_block()  # the first request signs in
        assert provider.get_status_config()["credentials"] == "API key (may read only)"


def test_a_key_that_may_not_delete_keeps_its_memories_and_artifacts(
    connected: FakePenfield, tmp_path: Path
) -> None:
    connected.api_key_scopes[KEY] = frozenset({"read", "write"})
    provider = _start(tmp_path)
    saved = json.loads(
        provider.handle_tool_call("penfield_save_artifact", {"path": "/n/a.md", "content": "x"})
    )
    assert saved.get("success") is True

    answer = json.loads(provider.handle_tool_call("penfield_delete_artifact", {"path": "/n/a.md"}))

    assert answer["error"].endswith("This Penfield login may read and write.")
    assert "/n/a.md" in connected.artifacts


def test_status_names_a_browser_sign_in_and_what_it_may_do(
    api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from penfield.auth import PenfieldAuth
    from penfield.config import PenfieldConfig

    monkeypatch.setenv("PENFIELD_URL", api.url)
    api.approve_sign_ins = True
    PenfieldAuth(PenfieldConfig.load(tmp_path), tmp_path).device_code_flow()

    bound = _start(tmp_path)
    assert bound.get_status_config()["credentials"] == (
        "browser sign-in (may read, write and delete)"
    )
    assert bound.is_available() is True
    bound.shutdown()


def test_a_sign_in_made_at_another_address_does_not_count(
    api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermes is told the profile is not connected, and no request goes out."""
    from penfield.auth import PenfieldAuth
    from penfield.config import PenfieldConfig

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PENFIELD_URL", api.url)
    api.approve_sign_ins = True
    PenfieldAuth(PenfieldConfig.load(tmp_path), tmp_path).device_code_flow()
    assert PenfieldMemoryProvider().is_available() is True

    with FakePenfield() as elsewhere:
        monkeypatch.setenv("PENFIELD_URL", elsewhere.url)
        provider = PenfieldMemoryProvider()
        assert provider.is_available() is False
        assert provider.get_status_config()["credentials"] == "none"
        assert elsewhere.calls() == []


def test_asking_whether_penfield_is_available_creates_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "untouched"
    profile.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(profile))
    assert PenfieldMemoryProvider().is_available() is False
    assert list(profile.iterdir()) == []
