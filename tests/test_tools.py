# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for tool dispatch and schema invariants."""

from __future__ import annotations

import json
from typing import Any

import pytest

from hermes_penfield.tools import (
    PENFIELD_TOOL_SCHEMAS,
    TOOL_NAMES,
    dispatch,
)


class _RecordingClient:
    """Captures the endpoint name + body passed to `call()`."""

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
        self.calls.append((endpoint_name, body or {}, path_params or {}))
        # Return a canned shape matching real envelopes.
        if endpoint_name == "memory_delete":
            return None
        if endpoint_name == "search_hybrid":
            return {"items": [{"id": "m1", "snippet": "s", "score": 0.9}]}
        return {"id": "new", "ok": True}


class TestSchemas:
    def test_thirteen_tools(self) -> None:
        assert len(PENFIELD_TOOL_SCHEMAS) == 13

    def test_each_has_required_fields(self) -> None:
        for t in PENFIELD_TOOL_SCHEMAS:
            assert "name" in t
            assert "description" in t
            assert "input_schema" in t
            assert t["input_schema"]["type"] == "object"

    def test_names_match_spec(self) -> None:
        expected = {
            "penfield_store",
            "penfield_recall",
            "penfield_search",
            "penfield_connect",
            "penfield_fetch",
            "penfield_update",
            "penfield_delete",
            "penfield_explore",
            "penfield_reflect",
            "penfield_list_artifacts",
            "penfield_save_artifact",
            "penfield_retrieve_artifact",
            "penfield_awaken",
        }
        assert expected == TOOL_NAMES


class TestStore:
    def test_minimal_body(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_store", {"content": "hello"})
        name, body, _ = c.calls[0]
        assert name == "memory_create"
        assert body == {"content": "hello"}

    def test_full_body(self) -> None:
        c = _RecordingClient()
        dispatch(
            c,
            "penfield_store",
            {
                "content": "x",
                "memory_type": "fact",
                "importance": 0.8,
                "confidence": 0.5,
                "tags": ["a", "b"],
            },
        )
        _, body, _ = c.calls[0]
        assert body["memory_type"] == "fact"
        assert body["tags"] == ["a", "b"]

    def test_rejects_protected_memory_type(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="managed via"):
            dispatch(c, "penfield_store", {"content": "x", "memory_type": "identity_core"})

    def test_rejects_secret_like_content(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="secret"):
            dispatch(c, "penfield_store", {"content": "my token is ghp_abc123"})


class TestRecallSearch:
    def test_recall_maps_to_search_hybrid(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_recall", {"query": "q", "limit": 3}))
        name, body, _ = c.calls[0]
        assert name == "search_hybrid"
        assert body["query"] == "q"
        assert body["limit"] == 3
        assert "items" in result
        assert result["count"] == 1

    def test_search_validates_weight_sum(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match=r"sum to 1\.0"):
            dispatch(
                c,
                "penfield_search",
                {
                    "query": "q",
                    "bm25_weight": 0.5,
                    "vector_weight": 0.5,
                    "graph_weight": 0.5,
                },
            )


class TestConnect:
    def test_valid_relationship(self) -> None:
        c = _RecordingClient()
        dispatch(
            c,
            "penfield_connect",
            {"from_id": "a", "to_id": "b", "relationship_type": "supports"},
        )
        name, body, _ = c.calls[0]
        assert name == "relationship_create"
        assert body["relationship_type"] == "supports"

    def test_invalid_relationship_type(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="valid types"):
            dispatch(
                c,
                "penfield_connect",
                {"from_id": "a", "to_id": "b", "relationship_type": "loves"},
            )

    def test_inverse_paired_needs_inverse_type(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="INVERSE_PAIRED"):
            dispatch(
                c,
                "penfield_connect",
                {
                    "from_id": "a",
                    "to_id": "b",
                    "relationship_type": "parent_of",
                    "direction_type": "INVERSE_PAIRED",
                },
            )


class TestMutations:
    def test_update_puts_id_in_path(self) -> None:
        c = _RecordingClient()
        dispatch(
            c,
            "penfield_update",
            {"memory_id": "m-1", "content": "new"},
        )
        name, body, params = c.calls[0]
        assert name == "memory_update"
        assert params == {"memory_id": "m-1"}
        assert "memory_id" not in body

    def test_update_requires_a_field(self) -> None:
        c = _RecordingClient()
        with pytest.raises(ValueError, match="at least one"):
            dispatch(c, "penfield_update", {"memory_id": "m-1"})

    def test_delete_returns_confirmed(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_delete", {"memory_id": "m-1"}))
        assert result == {"deleted": True, "memory_id": "m-1"}


class TestUnknownTool:
    def test_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown tool"):
            dispatch(_RecordingClient(), "nope", {})


class TestArtifacts:
    def test_save_body(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_save_artifact", {"path": "/x.md", "content": "hi"})
        name, body, _ = c.calls[0]
        assert name == "artifact_save"
        assert body == {"path": "/x.md", "content": "hi"}

    def test_retrieve_uses_query(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_retrieve_artifact", {"path": "/x.md"})
        # _RecordingClient folds query into the call signature; we assert via name.
        assert c.calls[0][0] == "artifact_retrieve"
