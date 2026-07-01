# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for tool dispatch and schema invariants — mirrors MCP surface."""

from __future__ import annotations

import json
from typing import Any

import pytest
from penfield.tools import (
    PENFIELD_TOOL_SCHEMAS,
    TOOL_NAMES,
    dispatch,
)


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []

    def call(
        self,
        endpoint_name: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> Any:
        self.calls.append((endpoint_name, body or {}, query or {}))
        if endpoint_name == "artifact_delete":
            return None
        if endpoint_name == "relationship_delete_between":
            return None
        if endpoint_name == "search_hybrid":
            return {"items": [{"id": "m1", "snippet": "s", "score": 0.9}]}
        return {"id": "new", "ok": True}


class TestSchemas:
    def test_seventeen_tools(self) -> None:
        assert len(PENFIELD_TOOL_SCHEMAS) == 17

    def test_each_has_required_fields(self) -> None:
        for t in PENFIELD_TOOL_SCHEMAS:
            assert "name" in t
            assert "description" in t
            assert "parameters" in t

    def test_names_match_mcp(self) -> None:
        expected = {
            "penfield_awaken",
            "penfield_store",
            "penfield_recall",
            "penfield_search",
            "penfield_fetch",
            "penfield_update_memory",
            "penfield_connect",
            "penfield_disconnect",
            "penfield_explore",
            "penfield_reflect",
            "penfield_save_artifact",
            "penfield_retrieve_artifact",
            "penfield_list_artifacts",
            "penfield_delete_artifact",
            "penfield_list_contexts",
            "penfield_restore_context",
            "penfield_save_context",
        }
        assert expected == TOOL_NAMES


class TestStore:
    def test_minimal_body(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_store", {"content": "hello"})
        name, body, _ = c.calls[0]
        assert name == "memory_create"
        assert body == {"content": "hello"}

    def test_rejects_secret_like_content(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="secret"):
            dispatch(c, "penfield_store", {"content": "my token is ghp_abc123"})


class TestRecall:
    def test_recall_maps_to_search_hybrid(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_recall", {"query": "q", "limit": 3}))
        name, body, _ = c.calls[0]
        assert name == "search_hybrid"
        assert body["query"] == "q"
        assert body["limit"] == 3
        assert result["count"] == 1


class TestFetch:
    def test_uses_mcp_param_name_id(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_fetch", {"id": "abc-123"})
        name, _, _ = c.calls[0]
        assert name == "memory_get"


class TestConnect:
    def test_mcp_param_names_map_to_api(self) -> None:
        c = _RecordingClient()
        dispatch(
            c,
            "penfield_connect",
            {"from_memory": "a", "to_memory": "b", "relationship_type": "supports"},
        )
        name, body, _ = c.calls[0]
        assert name == "relationship_create"
        # MCP from_memory/to_memory → API from_id/to_id
        assert body["from_id"] == "a"
        assert body["to_id"] == "b"
        assert body["relationship_type"] == "supports"

    def test_invalid_relationship_type(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="valid types"):
            dispatch(
                c,
                "penfield_connect",
                {"from_memory": "a", "to_memory": "b", "relationship_type": "loves"},
            )


class TestDisconnect:
    def test_disconnect_calls_delete_between(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(c, "penfield_disconnect", {"from_memory": "a", "to_memory": "b"})
        )
        name, _, query = c.calls[0]
        assert name == "relationship_delete_between"
        assert query["from_id"] == "a"
        assert query["to_id"] == "b"
        assert result == {"disconnected": True}


class TestExplore:
    def test_mcp_param_maps_to_api(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_explore", {"start_memory": "abc", "max_depth": 2})
        name, body, _ = c.calls[0]
        assert name == "relationship_traverse"
        # MCP start_memory → API start_memory_id
        assert body["start_memory_id"] == "abc"
        assert body["max_depth"] == 2


class TestUpdateMemory:
    def test_update_puts_id_in_path(self) -> None:
        c = _RecordingClient()
        dispatch(
            c,
            "penfield_update_memory",
            {"memory_id": "m-1", "content": "new"},
        )
        name, _body, _ = c.calls[0]
        assert name == "memory_update"

    def test_update_requires_a_field(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="at least one"):
            dispatch(c, "penfield_update_memory", {"memory_id": "m-1"})


class TestArtifacts:
    def test_save_body(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_save_artifact", {"path": "/x.md", "content": "hi"})
        name, body, _ = c.calls[0]
        assert name == "artifact_save"
        assert body == {"path": "/x.md", "content": "hi"}

    def test_delete_artifact(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_delete_artifact", {"path": "/old.md"}))
        name, _, query = c.calls[0]
        assert name == "artifact_delete"
        assert query["path"] == "/old.md"
        assert result == {"deleted": True, "path": "/old.md"}

    def test_retrieve_uses_query(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_retrieve_artifact", {"path": "/x.md"})
        assert c.calls[0][0] == "artifact_retrieve"


class TestUnknownTool:
    def test_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown tool"):
            dispatch(_RecordingClient(), "nope", {})


class TestListContexts:
    def test_list_contexts_queries_checkpoint_type(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_list_contexts", {"limit": 10})
        name, _, query = c.calls[0]
        assert name == "memory_list"
        assert query["memory_type"] == "checkpoint"
        assert query["per_page"] == 10

    def test_list_contexts_with_name_pattern(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(c, "penfield_list_contexts", {"name_pattern": "investigation"})
        )
        # name_pattern is filtered client-side; REST doesn't support it
        assert "contexts" in result

    def test_list_contexts_default_limit(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_list_contexts", {})
        _, _, query = c.calls[0]
        assert query["per_page"] == 20


class TestRestoreContext:
    def test_restore_awakening_special_case(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_restore_context", {"name": "awakening"})
        assert c.calls[0][0] == "personality_awakening"

    def test_restore_checkpoint_exact_match(self) -> None:
        c = _RecordingClient()

        def mock_call(endpoint, **kw):
            c.calls.append((endpoint, kw.get("body", {}), kw.get("query", {})))
            if endpoint == "memory_list":
                return {
                    "items": [{"id": "cp1", "content": json.dumps({"checkpoint_name": "test-cp"})}]
                }
            if endpoint == "memory_get" and kw.get("path_params", {}).get("memory_id") == "cp1":
                return {
                    "content": json.dumps(
                        {
                            "checkpoint_name": "test-cp",
                            "description": "test",
                            "memory_ids": [],
                            "referenced_memories": [],
                        }
                    )
                }
            return {"id": "m1", "content": "x"}

        c.call = mock_call
        result = json.loads(dispatch(c, "penfield_restore_context", {"name": "test-cp"}))
        assert c.calls[0][0] == "memory_list"
        assert "memories" in result

    def test_restore_nonexistent_returns_error(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_restore_context", {"name": "nonexistent"}))
        assert "error" in result


class TestRecallTagsTranslation:
    """Regression: tags must be translated from MCP array to REST comma string."""

    def test_tags_array_becomes_comma_string(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_recall", {"query": "q", "tags": ["python", "async"]})
        _, body, _ = c.calls[0]
        assert body["tags"] == "python,async", f"expected comma string, got {body['tags']!r}"

    def test_tags_single_element(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_recall", {"query": "q", "tags": ["python"]})
        _, body, _ = c.calls[0]
        assert body["tags"] == "python"

    def test_source_type_maps_to_source_types(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_recall", {"query": "q", "source_type": "memory"})
        _, body, _ = c.calls[0]
        assert body["source_types"] == ["memory"]
        assert "source_type" not in body

    def test_plain_query_limit_still_works(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_recall", {"query": "q", "limit": 5})
        _, body, _ = c.calls[0]
        assert body == {"query": "q", "limit": 5}


class TestSaveContext:
    """save_context: creates checkpoint with structured content + reference parsing."""

    def test_creates_checkpoint_with_name_and_tags(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(
                c,
                "penfield_save_context",
                {
                    "name": "Test Session",
                    "description": "Testing save_context",
                },
            )
        )
        name, body, _ = c.calls[-1]  # last call is the create
        assert name == "memory_create"
        assert body["memory_type"] == "checkpoint"
        assert body["importance"] == 0.9
        assert "Test Session" in body["tags"]
        assert result["success"] is True
        assert result["references_extracted"] == 0

    def test_parses_memory_id_references(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(
                c,
                "penfield_save_context",
                {
                    "name": "Ref Test",
                    "description": (
                        "See memory_id: 550e8400-e29b-41d4-a716-446655440000 for details"
                    ),
                },
            )
        )
        assert result["references_extracted"] == 1
        assert result["references_resolved"] == 1

    def test_parses_memory_phrase_references(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(
                c,
                "penfield_save_context",
                {
                    "name": "Phrase Test",
                    "description": "Related to memory: 'OAuth device flow'",
                },
            )
        )
        assert result["references_extracted"] == 1

    def test_rejects_duplicate_name(self) -> None:
        c = _RecordingClient()
        # Override the memory_list to return an existing checkpoint with same name
        original_call = c.call

        def mock_call(endpoint, **kw):
            if (
                endpoint == "memory_list"
                and kw.get("query", {}).get("memory_type") == "checkpoint"
            ):
                return {
                    "items": [
                        {"id": "existing", "content": json.dumps({"checkpoint_name": "Dup Name"})}
                    ]
                }
            return original_call(endpoint, **kw)

        c.call = mock_call
        result = json.loads(
            dispatch(
                c,
                "penfield_save_context",
                {
                    "name": "Dup Name",
                    "description": "should fail",
                },
            )
        )
        assert result["success"] is False
        assert result["error_code"] == "DUPLICATE_NAME"
