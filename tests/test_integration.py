# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Live tests against the real Penfield API.

The unit and Hermes tests run against ``tests/support/fake_penfield.py``.
These tests check the real API still behaves the way that fake does, and
run the plugin's tools and hooks against it end to end.

Run (they are skipped otherwise)::

    PENFIELD_ENV=dev PENFIELD_API_KEY=... pytest -m integration --no-cov

They write to the account the key belongs to, so:

* Only the dev environment is accepted, unless ``PENFIELD_ENV=prod`` comes
  with ``PENFIELD_TEST_ALLOW_PROD=1``.
* Everything created carries a ``hermes-penfield-int-*`` tag or lives under
  ``/hermes-penfield-integration/``, and is deleted afterwards.
* Deleting a memory needs an API key with the delete permission. Without
  it, tests that create memories are skipped. Set
  ``PENFIELD_TEST_ALLOW_LEFTOVERS=1`` to run them anyway on a throwaway
  account and leave the tagged memories behind.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from penfield.client import PenfieldClient
from penfield.constants import MEMORY_SOURCE_TYPES, SEMANTIC_SEARCH_WEIGHTS
from penfield.exceptions import NotFoundError, PenfieldError, ValidationError
from penfield.provider import TOOL_GUIDANCE, PenfieldMemoryProvider
from penfield.tools import REFLECT_WINDOWS, dispatch

pytestmark = pytest.mark.integration

TAG_PREFIX = "hermes-penfield-int"
ARTIFACT_DIR = "/hermes-penfield-integration"
#: Seconds allowed for a new memory to become searchable.
INDEX_WAIT = 20.0


def _tool(client: PenfieldClient, tool: str, /, **args: Any) -> Any:
    return json.loads(dispatch(client, tool, args))


def _eventually(check: Callable[[], Any], timeout: float = INDEX_WAIT) -> Any:
    """Return the first truthy result of ``check``, polling until ``timeout``."""
    deadline = time.monotonic() + timeout
    while True:
        result = check()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(1.0)


@pytest.fixture
def can_delete(live_client: PenfieldClient) -> bool:
    """Whether the key may delete memories, found without creating anything."""
    try:
        live_client.call(
            "memory_delete", path_params={"memory_id": "00000000-0000-0000-0000-000000000000"}
        )
    except NotFoundError:
        return True
    except PenfieldError:
        return False
    return True


@pytest.fixture
def tag() -> str:
    return f"{TAG_PREFIX}-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def remember(
    live_client: PenfieldClient, can_delete: bool, tag: str
) -> Iterator[Callable[..., dict[str, Any]]]:
    """Store memories through the tool; delete them when the test ends."""
    if not can_delete and not os.environ.get("PENFIELD_TEST_ALLOW_LEFTOVERS"):
        pytest.skip("this API key cannot delete memories; see the module docstring")
    created: list[str] = []

    def store(content: str, **extra: Any) -> dict[str, Any]:
        tags = [tag, *extra.pop("tags", [])]
        memory: dict[str, Any] = _tool(
            live_client, "penfield_store", content=f"{content} {tag}", tags=tags, **extra
        )
        created.append(memory["id"])
        return memory

    yield store
    if can_delete:
        for memory_id in created:
            live_client.call("memory_delete", path_params={"memory_id": memory_id})


class TestAuth:
    def test_api_key_exchanges_for_a_jwt(self, live_jwt: str) -> None:
        assert live_jwt.startswith("eyJ")

    def test_stats_endpoint_answers(self, live_client: PenfieldClient) -> None:
        assert "total_memories" in live_client.call("search_stats")

    def test_a_missing_memory_is_a_404(self, live_client: PenfieldClient) -> None:
        with pytest.raises(NotFoundError):
            live_client.call(
                "memory_get", path_params={"memory_id": "00000000-0000-0000-0000-000000000000"}
            )


class TestMemories:
    def test_store_fetch_update(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]], tag: str
    ) -> None:
        stored = remember("Lifecycle marker for the plugin tests.", importance=0.6)

        fetched = _tool(live_client, "penfield_fetch", id=stored["id"])
        updated = _tool(
            live_client, "penfield_update_memory", memory_id=stored["id"], importance=0.9
        )

        assert tag in fetched["content"]
        assert tag in fetched["tags"]
        assert fetched["memory_type"]
        assert updated["importance"] == 0.9

    def test_recall_returns_the_whole_memory_with_its_details(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]], tag: str
    ) -> None:
        text = "Recall marker. " + "The plugin tests wrote this sentence. " * 8 + "END-OF-MEMORY"
        stored = remember(text)

        def find() -> dict[str, Any] | None:
            items = _tool(live_client, "penfield_recall", query=f"recall marker {tag}")["items"]
            return next((it for it in items if it["id"] == stored["id"]), None)

        item = _eventually(find)

        assert item, "the stored memory never showed up in recall"
        assert "END-OF-MEMORY" in item["content"]
        assert tag in item["tags"]
        assert item["memory_type"]
        assert item["created_at"]
        assert 0 < item["score"] <= 1

    def test_recall_filters(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]], tag: str
    ) -> None:
        stored = remember("Filter marker for the plugin tests.")
        query = f"filter marker {tag}"

        def ids(**filters: Any) -> list[str]:
            found = _tool(live_client, "penfield_recall", query=query, **filters)
            return [it["id"] for it in found["items"]]

        assert _eventually(lambda: stored["id"] in ids(tags=[tag]))
        assert stored["id"] in ids(source_type="memory")
        assert stored["id"] not in ids(source_type="document")
        assert stored["id"] not in ids(tags=[f"{tag}-absent"])
        assert stored["id"] not in ids(start_date="2999-01-01")

    def test_the_memory_source_types_are_still_accepted(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]], tag: str
    ) -> None:
        """A list holding a value the API does not know returns nothing, without an error."""
        stored = remember("Source type marker for the plugin tests.")
        body = {
            "query": f"source type marker {tag}",
            "tags": tag,
            "source_types": list(MEMORY_SOURCE_TYPES),
        }

        def found() -> bool:
            items = live_client.call("search_hybrid", body=body)["items"]
            return stored["id"] in [it["id"] for it in items]

        assert _eventually(found)


class TestSemanticSearch:
    def test_the_api_applies_the_weights_the_search_tool_sends(
        self, live_client: PenfieldClient
    ) -> None:
        body = {"query": "plans and decisions", "limit": 3, **SEMANTIC_SEARCH_WEIGHTS}

        applied = live_client.call("search_hybrid", body=body)["analyzed_query"]["weights"]

        assert applied == {
            "bm25": SEMANTIC_SEARCH_WEIGHTS["bm25_weight"],
            "vector": SEMANTIC_SEARCH_WEIGHTS["vector_weight"],
            "graph": SEMANTIC_SEARCH_WEIGHTS["graph_weight"],
        }

    def test_search_finds_a_stored_memory(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]], tag: str
    ) -> None:
        stored = remember("Semantic search marker for the plugin tests.")

        def found() -> bool:
            result = _tool(live_client, "penfield_search", query=f"semantic search marker {tag}")
            return stored["id"] in [it["id"] for it in result["items"]]

        assert _eventually(found)


class TestGraph:
    def test_connect_explore_disconnect(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]]
    ) -> None:
        source, target = remember("Source node."), remember("Target node.")
        link = {"from_memory": source["id"], "to_memory": target["id"]}

        _tool(live_client, "penfield_connect", relationship_type="supports", strength=0.7, **link)
        explored = _tool(live_client, "penfield_explore", start_memory=source["id"], max_depth=2)
        removed = _tool(live_client, "penfield_disconnect", **link)

        assert target["id"] in json.dumps(explored)
        assert removed == {"disconnected": True}


class TestReflect:
    @pytest.mark.parametrize("window", REFLECT_WINDOWS)
    def test_every_time_window_is_accepted(self, live_client: PenfieldClient, window: str) -> None:
        assert "memories" in _tool(live_client, "penfield_reflect", time_window=window)

    def test_a_day_window_covers_a_memory_stored_just_now(
        self, live_client: PenfieldClient, remember: Callable[..., dict[str, Any]]
    ) -> None:
        stored = remember("Reflect marker for the plugin tests.")

        def reflected() -> bool:
            result = _tool(live_client, "penfield_reflect", time_window="1d")
            return stored["id"] in [m["id"] for m in result["memories"]]

        assert _eventually(reflected)

    def test_a_wrong_window_is_explained_by_the_api(self, live_client: PenfieldClient) -> None:
        with pytest.raises(ValidationError, match="time_window"):
            live_client.call("analysis_reflect", body={"time_window": "fortnight"})


class TestArtifacts:
    def test_save_list_retrieve_delete(self, live_client: PenfieldClient) -> None:
        name = f"note-{uuid.uuid4().hex[:8]}.md"
        path, content = f"{ARTIFACT_DIR}/{name}", "# integration test\nround trip"
        _tool(live_client, "penfield_save_artifact", path=path, content=content)
        try:
            listed = _tool(
                live_client,
                "penfield_list_artifacts",
                path_prefix=ARTIFACT_DIR,
                name_pattern=name[:13].upper(),
            )
            retrieved = _tool(live_client, "penfield_retrieve_artifact", path=path)
        finally:
            _tool(live_client, "penfield_delete_artifact", path=path)

        assert [f["name"] for f in listed["files"]] == [name]
        assert retrieved["content"] == content
        with pytest.raises(NotFoundError):
            dispatch(live_client, "penfield_retrieve_artifact", {"path": path})


class TestContexts:
    def test_save_list_restore(
        self,
        live_client: PenfieldClient,
        remember: Callable[..., dict[str, Any]],
        can_delete: bool,
        tag: str,
    ) -> None:
        member = remember("Checkpoint member for the plugin tests.")
        name = f"ctx-{tag}"

        saved = _tool(
            live_client,
            "penfield_save_context",
            name=name,
            description=f"Handoff for the plugin tests. See memory_id: {member['id']}.",
        )
        try:
            duplicate = _tool(live_client, "penfield_save_context", name=name, description="again")
            listed = _tool(live_client, "penfield_list_contexts", name_pattern=tag)
            by_name = _tool(live_client, "penfield_restore_context", name=name)
            by_id = _tool(live_client, "penfield_restore_context", name=saved["context_id"])
        finally:
            if can_delete:
                live_client.call("memory_delete", path_params={"memory_id": saved["context_id"]})

        assert saved["success"] is True
        assert saved["unresolved_references"] == []
        assert duplicate["error_code"] == "DUPLICATE_NAME"
        assert [c["name"] for c in listed["contexts"]] == [name]
        assert member["id"] in [m["id"] for m in by_name["memories"]]
        assert by_id["name"] == name


class TestProvider:
    @pytest.fixture
    def provider(self, live_jwt: str, tmp_path: Any) -> PenfieldMemoryProvider:
        instance = PenfieldMemoryProvider()
        assert instance.is_available()
        instance.initialize("live-session", hermes_home=str(tmp_path), platform="cli")
        return instance

    def test_briefing_reaches_the_system_prompt(self, provider: PenfieldMemoryProvider) -> None:
        block = provider.system_prompt_block()
        assert block.endswith(TOOL_GUIDANCE)
        assert block != TOOL_GUIDANCE, "the briefing did not arrive within the hot-path deadline"

    def test_auto_recall_finds_a_stored_memory(
        self,
        provider: PenfieldMemoryProvider,
        remember: Callable[..., dict[str, Any]],
        tag: str,
    ) -> None:
        stored = remember("Auto-recall marker for the plugin tests.")

        def recall() -> str:
            text = provider.prefetch(f"auto-recall marker {tag}")
            return text if stored["id"] in text else ""

        recalled = _eventually(recall)

        assert recalled.startswith("[Penfield memory: recalled context]")
        assert stored["id"] in recalled

    def test_awaken_tool_returns_the_briefing(self, provider: PenfieldMemoryProvider) -> None:
        result = json.loads(provider.handle_tool_call("penfield_awaken", {}))
        assert result.get("briefing")
