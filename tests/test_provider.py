# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""The provider's hooks, driven the way Hermes drives them."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from penfield.provider import AUTO_RECALL_ITEM_CHARS, PenfieldMemoryProvider
from penfield.tools import PENFIELD_TOOL_SCHEMAS

from tests.support.fake_penfield import FakePenfield

MESSAGES = [
    {"role": "user", "content": "Please plan the release checklist with me."},
    {"role": "assistant", "content": "Here is a first draft of the checklist."},
]


def _save_tokens(hermes_home: Path, *, source: str) -> None:
    """Write a token cache as a browser sign-in or an API key exchange leaves it."""
    cache = hermes_home / "penfield" / "tokens.json"
    cache.parent.mkdir(parents=True)
    tokens = {"access_token": "AT", "refresh_token": "RT", "expires_at": 4102444800.0}
    cache.write_text(json.dumps({**tokens, "source": source}), encoding="utf-8")


class TestIdentity:
    def test_name(self) -> None:
        assert PenfieldMemoryProvider().name == "penfield"

    def test_exposes_every_tool(self, provider: PenfieldMemoryProvider) -> None:
        names = [s["name"] for s in provider.get_tool_schemas()]
        assert names == [s["name"] for s in PENFIELD_TOOL_SCHEMAS]

    def test_is_a_hermes_memory_provider(self) -> None:
        base = pytest.importorskip("agent.memory_provider").MemoryProvider
        assert isinstance(PenfieldMemoryProvider(), base)


class TestAvailability:
    def test_unavailable_without_credentials(self) -> None:
        assert PenfieldMemoryProvider().is_available() is False

    def test_available_with_an_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        assert PenfieldMemoryProvider().is_available() is True

    def test_available_with_a_saved_browser_sign_in(self, hermes_home: Path) -> None:
        _save_tokens(hermes_home, source="oauth")
        assert PenfieldMemoryProvider().is_available() is True

    def test_tokens_left_by_a_removed_api_key_are_not_credentials(self, hermes_home: Path) -> None:
        _save_tokens(hermes_home, source="api_key")
        assert PenfieldMemoryProvider().is_available() is False

    def test_an_unreadable_token_file_is_not_credentials(self, hermes_home: Path) -> None:
        cache = hermes_home / "penfield" / "tokens.json"
        cache.parent.mkdir(parents=True)
        cache.write_text("not json", encoding="utf-8")
        assert PenfieldMemoryProvider().is_available() is False

    def test_never_touches_the_network(self, api: FakePenfield) -> None:
        PenfieldMemoryProvider().is_available()
        assert api.calls() == []


class TestSystemPrompt:
    def test_includes_briefing_and_tool_guidance(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        block = provider.system_prompt_block()
        assert block.startswith(api.briefing)
        assert "penfield_store" in block
        assert "penfield_recall" in block

    def test_tool_guidance_without_credentials(self, hermes_home: Path) -> None:
        provider = PenfieldMemoryProvider()
        provider.initialize("no-credentials", hermes_home=str(hermes_home))
        assert "penfield_recall" in provider.system_prompt_block()

    @pytest.mark.parametrize("request_that_fails", ["/auth/token", "/personality/awakening"])
    def test_one_failed_request_at_session_start_does_not_cost_the_briefing(
        self,
        api: FakePenfield,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        request_that_fails: str,
    ) -> None:
        """Hermes keeps the prompt it builds first, so a miss here lasts the whole session."""
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        method = "POST" if request_that_fails == "/auth/token" else "GET"
        api.fail_next[(method, request_that_fails)] = [503]
        fresh = PenfieldMemoryProvider()
        fresh.initialize("unit-session", hermes_home=str(tmp_path), platform="cli")

        assert fresh.system_prompt_block().startswith(api.briefing)

    def test_a_briefing_that_could_not_be_loaded_is_logged_once(
        self,
        api: FakePenfield,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        api.outage = "errors"
        caplog.set_level(logging.WARNING)
        fresh = PenfieldMemoryProvider()
        fresh.initialize("unit-session", hermes_home=str(tmp_path), platform="cli")

        first, second = fresh.system_prompt_block(), fresh.system_prompt_block()

        assert api.briefing not in first + second
        said = [
            r.getMessage() for r in caplog.records if "briefing was not loaded" in r.getMessage()
        ]
        assert len(said) == 1
        assert "this session runs without it" in said[0]

    def test_an_account_without_a_briefing_is_not_a_problem(
        self,
        api: FakePenfield,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        api.briefing = ""
        caplog.set_level(logging.WARNING)
        fresh = PenfieldMemoryProvider()
        fresh.initialize("unit-session", hermes_home=str(tmp_path), platform="cli")

        assert "penfield_recall" in fresh.system_prompt_block()
        assert [r for r in caplog.records if "briefing" in r.getMessage()] == []


class TestAutoRecall:
    def test_empty_when_nothing_matches(self, provider: PenfieldMemoryProvider) -> None:
        assert provider.prefetch("anything at all?") == ""

    def test_lists_each_memory_with_id_type_and_date(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        memory_id = api.seed_memory("The release plan has four steps.", memory_type="insight")

        header, line = provider.prefetch("what is the release plan?").splitlines()

        assert header == "[Penfield memory: recalled context]"
        assert line == f"- [{memory_id}] (insight, 2026-01-01) The release plan has four steps."

    def test_long_memories_are_cut_with_a_pointer(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        api.seed_memory("release plan " + "detail " * 200)
        provider._config.max_prefetch_chars = 10_000  # type: ignore[union-attr]

        line = provider.prefetch("what is the release plan?").splitlines()[1]

        assert len(line) < AUTO_RECALL_ITEM_CHARS + 150
        assert line.endswith("(penfield_fetch this id for the rest)")

    def test_total_size_is_capped(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        for i in range(5):
            api.seed_memory(f"release plan part {i} " + "detail " * 200)

        text = provider.prefetch("what is the release plan?")

        assert len(text) <= provider._config.max_prefetch_chars  # type: ignore[union-attr]

    def test_can_be_turned_off(self, api: FakePenfield, provider: PenfieldMemoryProvider) -> None:
        api.seed_memory("The release plan has four steps.")
        provider._config.auto_recall = False  # type: ignore[union-attr]
        api.reset_calls()

        assert provider.prefetch("what is the release plan?") == ""
        assert api.calls() == []

    def test_blank_query_is_not_searched(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        api.reset_calls()
        assert provider.prefetch("   ") == ""
        assert api.calls("POST", "/search/hybrid") == []


class TestPreCompress:
    @pytest.fixture(autouse=True)
    def _switched_on(self, provider: PenfieldMemoryProvider) -> None:
        assert provider._config is not None
        provider._config.pre_compress_save = True

    def test_off_unless_the_user_turns_it_on(
        self, api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        fresh = PenfieldMemoryProvider()
        fresh.initialize("unit-session", hermes_home=str(tmp_path / "fresh"), platform="cli")

        assert fresh.on_pre_compress(MESSAGES) == ""
        assert api.calls("POST", "/memories") == []

    def test_saves_a_tagged_checkpoint_and_returns_a_pointer(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        pointer = provider.on_pre_compress(MESSAGES)

        (checkpoint,) = [m for m in api.memories.values() if m["memory_type"] == "checkpoint"]
        saved = json.loads(checkpoint["content"])
        assert saved["checkpoint_name"].startswith("pre-compress-unit-session-")
        assert saved["description"] == "Please plan the release checklist with me."
        assert "auto-checkpoint" in checkpoint["tags"]
        assert saved["checkpoint_name"] in pointer
        assert "penfield_restore_context" in pointer

    def test_does_not_scan_existing_checkpoints(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        provider.on_pre_compress(MESSAGES)
        assert api.calls("GET", "/memories") == []

    def test_can_be_turned_off(self, api: FakePenfield, provider: PenfieldMemoryProvider) -> None:
        provider._config.pre_compress_save = False  # type: ignore[union-attr]

        assert provider.on_pre_compress(MESSAGES) == ""
        assert api.calls("POST", "/memories") == []

    @pytest.mark.parametrize("context", ["subagent", "cron", "flush"])
    def test_only_the_primary_agent_writes(
        self, api: FakePenfield, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context: str
    ) -> None:
        monkeypatch.setenv("PENFIELD_URL", api.url)
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_pf_test_ak_unit")
        (tmp_path / "penfield").mkdir(exist_ok=True)
        (tmp_path / "penfield" / "config.json").write_text('{"pre_compress_save": true}')
        provider = PenfieldMemoryProvider()
        provider.initialize("child", hermes_home=str(tmp_path), agent_context=context)

        assert provider.on_pre_compress(MESSAGES) == ""
        assert api.calls("POST", "/memories") == []

    def test_a_failed_save_returns_nothing(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        api.fail_next[("POST", "/memories")] = [503]
        assert provider.on_pre_compress(MESSAGES) == ""

    def test_follows_a_session_switch(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        provider.on_session_switch("resumed-session", parent_session_id="unit-session")

        assert "pre-compress-resumed-session-" in provider.on_pre_compress(MESSAGES)


class TestSetup:
    def test_setup_asks_only_for_the_api_key(self) -> None:
        (field,) = PenfieldMemoryProvider().get_config_schema()
        assert field["key"] == "api_key"
        assert field["secret"] is True
        assert field["env_var"] == "PENFIELD_API_KEY"
        assert field["url"].startswith("https://")

    def test_save_config_writes_settings_but_never_the_key(self, hermes_home: Path) -> None:
        PenfieldMemoryProvider().save_config(
            {"penfield_env": "dev", "api_key": "tm_pf_test_ak_unit"}, str(hermes_home)
        )
        saved = json.loads((hermes_home / "penfield" / "config.json").read_text())
        assert saved["penfield_env"] == "dev"
        assert "tm_pf_test_ak_unit" not in json.dumps(saved)

    def test_unavailable_reason_points_at_setup(self) -> None:
        reason = PenfieldMemoryProvider().unavailable_reason()
        assert "hermes memory setup" in reason
        assert "API key" in reason


class TestStatus:
    def test_reports_configuration_before_initialize(self, hermes_home: Path) -> None:
        status = PenfieldMemoryProvider().get_status_config({})
        assert status["environment"] == "prod"
        assert status["api"] == "https://api.penfield.app/api/v2"
        assert status["credentials"] == "none"
        assert status["auto_recall"].startswith("on")
        assert status["mirror_builtin_memory"] == "off"

    def test_names_the_credential_in_use_without_showing_it(
        self, provider: PenfieldMemoryProvider
    ) -> None:
        status = provider.get_status_config({})
        assert status["credentials"] == "API key"
        assert "tm_pf_test_ak_unit" not in json.dumps(status)

    def test_reports_a_saved_login(self, hermes_home: Path) -> None:
        _save_tokens(hermes_home, source="oauth")
        assert PenfieldMemoryProvider().get_status_config({})["credentials"] == "saved login"

    def test_tokens_left_by_a_removed_api_key_are_not_a_login(self, hermes_home: Path) -> None:
        _save_tokens(hermes_home, source="api_key")
        assert PenfieldMemoryProvider().get_status_config({})["credentials"] == "none"


class TestToolCalls:
    def test_result_is_json(self, provider: PenfieldMemoryProvider) -> None:
        result = json.loads(provider.handle_tool_call("penfield_store", {"content": "a fact"}))
        assert result["content"] == "a fact"

    def test_unknown_tool_is_an_error_the_model_can_read(
        self, provider: PenfieldMemoryProvider
    ) -> None:
        result = json.loads(provider.handle_tool_call("penfield_nope", {}))
        assert "unknown tool" in result["error"].lower()
        assert result["tool"] == "penfield_nope"

    def test_missing_argument_is_named(self, provider: PenfieldMemoryProvider) -> None:
        result = json.loads(provider.handle_tool_call("penfield_store", {}))
        assert "content" in result["error"]

    def test_api_rejection_is_reported(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        api.fail_next[("POST", "/memories")] = [422]
        result = json.loads(provider.handle_tool_call("penfield_store", {"content": "x"}))
        assert "error" in result

    def test_extra_keyword_arguments_from_hermes_are_ignored(
        self, provider: PenfieldMemoryProvider
    ) -> None:
        raw = provider.handle_tool_call(
            "penfield_store", {"content": "a fact"}, hermes_home="/x", agent_identity="main"
        )
        assert "error" not in json.loads(raw)


class TestRegistration:
    def test_register_hands_hermes_a_new_provider_each_time(self) -> None:
        import penfield

        class Context:
            def __init__(self) -> None:
                self.providers: list[object] = []

            def register_memory_provider(self, provider: object) -> None:
                self.providers.append(provider)

        ctx = Context()
        penfield.register(ctx)
        penfield.register(ctx)

        first, second = ctx.providers
        assert isinstance(first, PenfieldMemoryProvider)
        assert first is not second


class TestLifecycle:
    def test_turn_sync_and_teardown_do_nothing_visible(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        api.reset_calls()
        provider.sync_turn("user text", "assistant text", session_id="unit-session")
        provider.queue_prefetch("next topic")
        provider.on_session_end(MESSAGES)
        provider.shutdown()
        assert api.calls() == []

    def test_tool_call_before_initialize_reports_an_error(self) -> None:
        result = json.loads(PenfieldMemoryProvider().handle_tool_call("penfield_recall", {}))
        assert "not initialized" in result["error"]
