# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Context checkpoints: saving, listing and restoring across many checkpoints."""

from __future__ import annotations

import json
from typing import Any

from penfield.client import PenfieldClient
from penfield.tools import dispatch

from tests.support.fake_penfield import FakePenfield

CHECKPOINTS = 150  # more than one API page (100 per page)


def _call(client: PenfieldClient, tool: str, **args: Any) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(dispatch(client, tool, args))
    return result


def _seed_checkpoints(api: FakePenfield, count: int = CHECKPOINTS) -> list[str]:
    """Seed ``count`` checkpoints, oldest first; returns their names."""
    member = api.seed_memory("a memory every checkpoint refers to")
    names = [f"ctx-{i:03d}" for i in range(count)]
    for name in names:
        api.seed_checkpoint(name, description=f"handoff {name}", memory_ids=[member])
    return names


def test_save_context_rejects_a_name_used_by_an_old_checkpoint(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    names = _seed_checkpoints(api)

    result = _call(api_client, "penfield_save_context", name=names[0], description="again")

    assert result["success"] is False
    assert result["error_code"] == "DUPLICATE_NAME"


def test_list_contexts_filters_by_name_across_all_pages(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    _seed_checkpoints(api)

    result = _call(api_client, "penfield_list_contexts", name_pattern="ctx-01", limit=20)

    assert sorted(c["name"] for c in result["contexts"]) == [f"ctx-{i:03d}" for i in range(10, 20)]
    assert result["total"] == 10


def test_list_contexts_offset_skips_exactly_that_many(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    names = _seed_checkpoints(api)
    newest_first = names[::-1]

    result = _call(api_client, "penfield_list_contexts", limit=20, offset=30)

    assert [c["name"] for c in result["contexts"]] == newest_first[30:50]
    assert result["total"] == CHECKPOINTS


def test_restore_context_honours_limit(api: FakePenfield, api_client: PenfieldClient) -> None:
    ids = [api.seed_memory(f"memory number {i}") for i in range(5)]
    api.seed_checkpoint("handoff", description="five memories", memory_ids=ids)

    limited = _call(api_client, "penfield_restore_context", name="handoff", limit=2)
    whole = _call(api_client, "penfield_restore_context", name="handoff", limit=20)

    assert (limited["count"], limited["truncated"]) == (2, True)
    assert (whole["count"], whole["truncated"]) == (5, False)


def test_restore_context_returns_whole_memories(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    long_text = "Decision record. " + "Reasoning continues. " * 30 + "FINAL-SENTENCE"
    api.seed_checkpoint("handoff", memory_ids=[api.seed_memory(long_text)])

    result = _call(api_client, "penfield_restore_context", name="handoff")

    assert result["memories"][0]["content"] == long_text


def test_restore_context_accepts_a_checkpoint_id(
    api: FakePenfield, api_client: PenfieldClient
) -> None:
    member = api.seed_memory("the one memory")
    checkpoint_id = api.seed_checkpoint("handoff", memory_ids=[member])

    result = _call(api_client, "penfield_restore_context", name=checkpoint_id)

    assert result["name"] == "handoff"
    assert result["count"] == 1


class TestSaveContext:
    DESCRIPTION = "Launch checklist handoff for the next session."

    def test_stores_a_checkpoint_memory(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        linked = api.seed_memory("The launch checklist has four steps.")

        result = _call(
            api_client, "penfield_save_context", name="handoff", description=self.DESCRIPTION
        )

        stored = api.memories[result["context_id"]]
        payload = json.loads(stored["content"])
        assert result["success"] is True
        assert stored["memory_type"] == "checkpoint"
        assert stored["tags"] == ["checkpoint", "context", "handoff"]
        assert payload["checkpoint_name"] == "handoff"
        assert payload["description"] == self.DESCRIPTION
        assert payload["memory_ids"] == [linked]

    def test_a_name_that_cannot_be_a_tag_is_still_the_name(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        """The name is also sent as a tag, where Penfield can store it as one."""
        name = "Handoff: " + "n" * 60

        result = _call(api_client, "penfield_save_context", name=name, description="Notes.")

        (request,) = api.calls("POST", "/memories")
        restored = _call(api_client, "penfield_restore_context", name=name)
        assert request.body["tags"] == ["checkpoint", "context"]
        assert json.loads(api.memories[result["context_id"]]["content"])["checkpoint_name"] == name
        assert restored["name"] == name

    def test_links_memories_referenced_by_id(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        referenced = api.seed_memory("An unrelated note about billing.")

        result = _call(
            api_client,
            "penfield_save_context",
            name="handoff",
            description=f"{self.DESCRIPTION} See memory_id: {referenced.upper()}.",
        )

        assert result["referenced_memories"] == [referenced]
        assert (result["references_extracted"], result["references_resolved"]) == (1, 1)
        assert result["unresolved_references"] == []

    def test_reports_an_id_that_does_not_exist(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        missing = "00000000-0000-4000-8000-00000000dead"

        result = _call(
            api_client,
            "penfield_save_context",
            name="handoff",
            description=f"{self.DESCRIPTION} See memory_id: {missing}.",
        )

        assert result["success"] is True
        assert result["referenced_memories"] == []
        assert result["unresolved_references"] == [f"memory_id: {missing}"]
        assert (result["references_extracted"], result["references_resolved"]) == (1, 0)

    def test_links_memories_referenced_by_phrase(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        referenced = api.seed_memory("Billing migration notes for the finance team.")

        result = _call(
            api_client,
            "penfield_save_context",
            name="handoff",
            description=f"{self.DESCRIPTION} Also memory: 'billing migration'.",
        )

        assert referenced in result["referenced_memories"] + result["memory_ids"]
        assert result["unresolved_references"] == []

    def test_reports_a_phrase_that_matches_nothing(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        result = _call(
            api_client,
            "penfield_save_context",
            name="handoff",
            description=f"{self.DESCRIPTION} Also memory: 'zzqx vvkw'.",
        )

        assert result["unresolved_references"] == ["memory: 'zzqx vvkw'"]

    def test_a_short_description_links_the_most_recent_memories(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        ids = [api.seed_memory(f"note {i}") for i in range(25)]

        result = _call(api_client, "penfield_save_context", name="handoff", description="wip")

        assert result["memory_ids"] == ids[::-1][:20]
        assert api.calls("POST", "/search/hybrid") == []

    def test_a_failed_search_falls_back_to_recent_memories(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        newest = api.seed_memory("anything at all")
        api.fail_next[("POST", "/search/hybrid")] = [500, 500, 500]

        result = _call(
            api_client, "penfield_save_context", name="handoff", description=self.DESCRIPTION
        )

        assert result["success"] is True
        assert result["memory_ids"] == [newest]

    def test_name_is_required(self, api_client: PenfieldClient) -> None:
        import pytest

        with pytest.raises(ValueError, match="name"):
            dispatch(api_client, "penfield_save_context", {"description": "no name"})
