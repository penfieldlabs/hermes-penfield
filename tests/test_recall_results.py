# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""What recall hands back to the agent, by tool call and by auto-recall."""

from __future__ import annotations

import json
from typing import Any

import pytest
from penfield.client import PenfieldClient
from penfield.provider import PenfieldMemoryProvider
from penfield.tools import dispatch

from tests.support.fake_penfield import SOURCE_TYPES, FakePenfield

TAIL = "TAIL-PAST-THE-SNIPPET"
LONG = (
    "The release plan covers tests, docs and the tag. " + "More detail on the plan. " * 12 + TAIL
)


def _recall(client: PenfieldClient, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(dispatch(client, "penfield_recall", args))
    return result


def test_recall_returns_whole_memories(api: FakePenfield, api_client: PenfieldClient) -> None:
    memory_id = api.seed_memory(LONG, tags=["release"], memory_type="insight", importance=0.9)

    (item,) = _recall(api_client, query="release plan")["items"]

    assert item["id"] == memory_id
    assert item["content"] == LONG
    assert item["memory_type"] == "insight"
    assert item["tags"] == ["release"]
    assert item["importance"] == 0.9
    assert item["created_at"]
    assert 0 < item["score"] <= 1


def test_recall_caps_very_long_memories(api: FakePenfield, api_client: PenfieldClient) -> None:
    api.seed_memory("release plan " + "x" * 6000)

    (item,) = _recall(api_client, query="release plan")["items"]

    assert len(item["content"]) <= 2000
    assert item["truncated"] is True


DOCUMENT = [
    "Release plan handbook, part one: tests run first.",
    "Release plan handbook, part two: docs are checked next.",
    "Release plan handbook, part three: the tag is pushed last.",
]


def test_memory_filter_leaves_documents_out(api: FakePenfield, api_client: PenfieldClient) -> None:
    memory_id = api.seed_memory("release plan agreed in a chat")
    api.seed_document("handbook.md", DOCUMENT)

    result = _recall(api_client, query="release plan", source_type="memory")

    assert [it["id"] for it in result["items"]] == [memory_id]


def test_document_filter_returns_only_documents(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    api.seed_memory("release plan agreed in a chat")
    chunk_ids = api.seed_document("handbook.md", DOCUMENT)

    result = _recall(api_client, query="release plan", source_type="document")

    assert sorted(it["id"] for it in result["items"]) == sorted(chunk_ids)


def test_filters_send_only_source_types_the_api_filters_on(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    api.seed_memory("release plan agreed in a chat")

    _recall(api_client, query="release plan", source_type="memory")

    (request,) = api.calls("POST", "/search/hybrid")
    assert set(request.body["source_types"]) <= SOURCE_TYPES
    assert "document_upload" not in request.body["source_types"]


def test_memory_filter_survives_a_source_type_the_api_stops_accepting(
    api: FakePenfield, api_client: PenfieldClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API answers such a search with nothing at all, and no error."""
    monkeypatch.setattr("tests.support.fake_penfield.SOURCE_TYPES", frozenset({"document_upload"}))
    memory_id = api.seed_memory("release plan agreed in a chat")
    api.seed_document("handbook.md", DOCUMENT)

    result = _recall(api_client, query="release plan", source_type="memory", limit=5)

    assert [it["id"] for it in result["items"]] == [memory_id]
    filtered, unfiltered = api.calls("POST", "/search/hybrid")
    assert "source_types" in filtered.body
    assert "source_types" not in unfiltered.body


@pytest.mark.parametrize("source_type", ["direct_input", "web_scrape", "everything"])
def test_other_source_types_are_refused(api_client: PenfieldClient, source_type: str) -> None:
    with pytest.raises(ValueError, match="'memory' or 'document'"):
        _recall(api_client, query="release plan", source_type=source_type)


def test_a_document_hit_carries_its_file_and_surrounding_text(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    api.seed_document("handbook.md", DOCUMENT)

    result = _recall(api_client, query="docs are checked", source_type="document", limit=1)

    (item,) = result["items"]
    assert item["source_type"] == "document"
    assert item["filename"] == "handbook.md"
    assert item["document_id"]
    assert (item["chunk_index"], item["total_chunks"]) == (1, 3)
    assert "tests run first" in item["content"]
    assert "the tag is pushed last" in item["content"]


def test_a_memory_hit_has_no_document_fields(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    api.seed_memory("release plan agreed in a chat")

    (item,) = _recall(api_client, query="release plan")["items"]

    assert "source_type" not in item
    assert "filename" not in item


def test_auto_recall_shows_ids_the_agent_can_fetch(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    memory_id = api.seed_memory(LONG)

    text = provider.prefetch("what is the release plan?")

    assert memory_id in text
    assert "The release plan covers tests, docs and the tag." in text


def test_auto_recall_applies_the_configured_score_threshold(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    api.seed_memory(LONG)

    provider.prefetch("what is the release plan?")

    (request,) = api.calls("POST", "/search/hybrid")
    assert request.body["min_score_threshold"] == provider._config.recall_threshold
    assert "importance_threshold" not in request.body


def test_auto_recall_leaves_saved_checkpoints_out(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    memory_id = api.seed_memory("The release plan has five steps.")
    for n in range(8):
        api.seed_checkpoint(f"release plan handoff {n}", description="release plan", memory_ids=[])

    recalled = provider.prefetch("What is the release plan?")

    assert memory_id in recalled
    assert "checkpoint_name" not in recalled


def test_auto_recall_names_the_document_a_hit_came_from(
    api: FakePenfield, provider: PenfieldMemoryProvider
) -> None:
    api.seed_document("handbook.md", DOCUMENT)

    recalled = provider.prefetch("When are the docs checked in the release plan?")

    assert "(document handbook.md, " in recalled


class TestSemanticSearch:
    """penfield_search ranks by meaning; penfield_recall balances it with keywords."""

    QUERY = "deployment schedule decision"

    @pytest.fixture
    def memories(self, api: FakePenfield) -> tuple[str, str]:
        by_words = api.seed_memory("The cafeteria posted its deployment schedule for soup.")
        by_meaning = api.seed_memory(
            "We agreed to ship the release on Friday once the smoke tests pass.",
            about=["deployment", "schedule", "decision"],
        )
        return by_words, by_meaning

    def _ids(self, client: PenfieldClient, tool: str) -> list[str]:
        result = json.loads(dispatch(client, tool, {"query": self.QUERY}))
        return [item["id"] for item in result["items"]]

    def test_recall_puts_the_keyword_match_first(
        self, api_client: PenfieldClient, memories: tuple[str, str]
    ) -> None:
        by_words, by_meaning = memories
        assert self._ids(api_client, "penfield_recall") == [by_words, by_meaning]

    def test_search_puts_the_match_in_meaning_first(
        self, api_client: PenfieldClient, memories: tuple[str, str]
    ) -> None:
        by_words, by_meaning = memories
        assert self._ids(api_client, "penfield_search") == [by_meaning, by_words]

    def test_search_sends_weights_that_lean_on_meaning_and_sum_to_one(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        dispatch(api_client, "penfield_search", {"query": self.QUERY, "limit": 3})

        (request,) = api.calls("POST", "/search/hybrid")
        weights = [request.body[k] for k in ("bm25_weight", "vector_weight", "graph_weight")]
        assert request.body["vector_weight"] > 0.5 > request.body["bm25_weight"]
        assert sum(weights) == pytest.approx(1.0)
        assert request.body["limit"] == 3

    def test_recall_leaves_the_weights_to_the_api(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        dispatch(api_client, "penfield_recall", {"query": self.QUERY})

        (request,) = api.calls("POST", "/search/hybrid")
        assert not {"bm25_weight", "vector_weight", "graph_weight"} & set(request.body)

    def test_auto_recall_leaves_the_weights_to_the_api(
        self, api: FakePenfield, provider: PenfieldMemoryProvider
    ) -> None:
        provider.prefetch(self.QUERY)

        (request,) = api.calls("POST", "/search/hybrid")
        assert "vector_weight" not in request.body
