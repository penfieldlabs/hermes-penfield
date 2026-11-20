# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""How the plugin behaves when the API misbehaves."""

from __future__ import annotations

import json
import time

import pytest
from penfield.client import PenfieldClient
from penfield.exceptions import APIError
from penfield.provider import TOOL_GUIDANCE, PenfieldMemoryProvider
from penfield.tools import dispatch

from tests.support.fake_penfield import FakePenfield


def test_a_failed_write_is_not_sent_twice(api: FakePenfield, api_client: PenfieldClient) -> None:
    """Retrying a POST whose first attempt may have landed duplicates the memory."""
    api.fail_next[("POST", "/memories")] = [503]

    with pytest.raises(APIError):
        api_client.call("memory_create", body={"content": "store me once"})

    assert len(api.calls("POST", "/memories")) == 1


def test_a_failed_read_is_retried(api: FakePenfield, api_client: PenfieldClient) -> None:
    memory_id = api.seed_memory("read me")
    api.fail_next[("GET", f"/memories/{memory_id}")] = [503]

    result = api_client.call("memory_get", path_params={"memory_id": memory_id})

    assert result["content"] == "read me"
    assert len(api.calls("GET", f"/memories/{memory_id}")) == 2


def test_auto_recall_gives_up_after_one_failed_attempt(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    api.fail_next[("POST", "/search/hybrid")] = [503, 503, 503]

    assert provider.prefetch("anything relevant?") == ""

    assert len(api.calls("POST", "/search/hybrid")) == 1


def test_briefing_is_fetched_once_and_reused(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    first = provider.system_prompt_block()
    second = provider.system_prompt_block()

    assert api.briefing in first
    assert second == first
    assert len(api.calls("GET", "/personality/awakening")) == 1


def test_briefing_gets_a_second_attempt(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    """Hermes keeps the prompt for the whole session, so one hiccup must not cost the briefing."""
    api.fail_next[("GET", "/personality/awakening")] = [503]

    assert api.briefing in provider.system_prompt_block()
    assert len(api.calls("GET", "/personality/awakening")) == 2


def test_system_prompt_survives_a_failing_api(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    api.fail_next[("GET", "/personality/awakening")] = [503, 503, 503]

    block = provider.system_prompt_block()

    assert block == TOOL_GUIDANCE
    assert len(api.calls("GET", "/personality/awakening")) == 2


def test_session_start_does_not_wait_past_the_briefing_deadline(
    api: FakePenfield, provider: PenfieldMemoryProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("penfield.provider.BRIEFING_DEADLINE", 0.3)
    api.outage = "hang"

    started = time.monotonic()
    block = provider.system_prompt_block()

    assert time.monotonic() - started < 1.0
    assert block == TOOL_GUIDANCE


def test_a_turn_does_not_wait_past_the_recall_deadline(
    api: FakePenfield, provider: PenfieldMemoryProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("penfield.provider.RECALL_DEADLINE", 0.3)
    api.seed_memory("The release plan has five steps.")
    api.outage = "hang"

    started = time.monotonic()
    recalled = provider.prefetch("What is the release plan?")

    assert time.monotonic() - started < 1.0
    assert recalled == ""


def test_compression_does_not_wait_past_the_checkpoint_deadline(
    api: FakePenfield, provider: PenfieldMemoryProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("penfield.provider.CHECKPOINT_DEADLINE", 0.3)
    assert provider._config is not None
    provider._config.pre_compress_save = True
    api.outage = "hang"

    started = time.monotonic()
    pointer = provider.on_pre_compress([{"role": "user", "content": "Plan the release with me."}])

    assert time.monotonic() - started < 1.0
    assert pointer == "", "no pointer to a checkpoint that has not been saved"


@pytest.mark.parametrize(
    "text",
    [
        "Risk-adjusted returns improved this quarter.",
        "The task-list is on the desk-side monitor.",
        "Notes from Nakia about the Akiak office.",
        "Ask-first policy applies to every disk-space alert.",
    ],
)
def test_store_accepts_prose_that_resembles_key_prefixes(
    api: FakePenfield, api_client: PenfieldClient, text: str
) -> None:
    result = json.loads(dispatch(api_client, "penfield_store", {"content": text}))

    assert result["id"] in api.memories


def test_missing_required_argument_names_the_argument(api_client: PenfieldClient) -> None:
    with pytest.raises(ValueError, match="penfield_store: missing required argument 'content'"):
        dispatch(api_client, "penfield_store", {})
