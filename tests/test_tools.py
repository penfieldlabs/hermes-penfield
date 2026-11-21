# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Each tool turns its arguments into the right API request."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from penfield.client import PenfieldClient
from penfield.tools import dispatch

from tests.support.fake_penfield import FakePenfield


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


class TestStore:
    def test_minimal_body(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_store", {"content": "hello"})
        name, body, _ = c.calls[0]
        assert name == "memory_create"
        assert body == {"content": "hello"}


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

    def test_list_contexts_with_name_pattern(self) -> None:
        c = _RecordingClient()
        result = json.loads(
            dispatch(c, "penfield_list_contexts", {"name_pattern": "investigation"})
        )
        # name_pattern is filtered client-side; REST doesn't support it
        assert "contexts" in result

    def test_list_contexts_default_limit(self) -> None:
        c = _RecordingClient()
        result = json.loads(dispatch(c, "penfield_list_contexts", {}))
        assert result["limit"] == 20


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
        assert "direct_input" in body["source_types"]
        assert "document_upload" not in body["source_types"]
        assert "source_type" not in body

    def test_plain_query_limit_still_works(self) -> None:
        c = _RecordingClient()
        dispatch(c, "penfield_recall", {"query": "q", "limit": 5})
        _, body, _ = c.calls[0]
        assert body == {"query": "q", "limit": 5}


class TestReflectWindows:
    @pytest.mark.parametrize("window", ["recent", "today", "week", "month"])
    def test_named_windows_are_sent_as_they_are(
        self, window: str, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        result = json.loads(dispatch(api_client, "penfield_reflect", {"time_window": window}))

        assert result["time_window"] == window
        (request,) = api.calls("POST", "/analysis/reflect")
        assert request.body == {"time_window": window}

    @pytest.mark.parametrize(("window", "days"), [("1d", 1), ("7d", 7), ("30d", 30), ("90d", 90)])
    def test_day_windows_are_sent_as_a_start_date(
        self, window: str, days: int, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        before = datetime.now(UTC) - timedelta(days=days, seconds=5)

        dispatch(api_client, "penfield_reflect", {"time_window": window})

        (request,) = api.calls("POST", "/analysis/reflect")
        assert set(request.body) == {"start_date"}
        sent = datetime.strptime(request.body["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        assert before <= sent <= datetime.now(UTC) - timedelta(days=days) + timedelta(seconds=5)

    def test_a_day_window_only_returns_memories_from_those_days(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        now = datetime.now(UTC)
        recent = api.seed_memory("new", created_at=(now - timedelta(days=2)).isoformat())
        api.seed_memory("old", created_at=(now - timedelta(days=20)).isoformat())

        result = json.loads(dispatch(api_client, "penfield_reflect", {"time_window": "7d"}))

        assert [m["id"] for m in result["memories"]] == [recent]

    def test_explicit_dates_win_over_a_day_window(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        dispatch(api_client, "penfield_reflect", {"time_window": "7d", "start_date": "2026-01-01"})

        (request,) = api.calls("POST", "/analysis/reflect")
        assert request.body == {"start_date": "2026-01-01"}

    def test_an_unknown_window_is_refused_before_any_request(
        self, api: FakePenfield, api_client: PenfieldClient
    ) -> None:
        with pytest.raises(ValueError, match="time_window must be one of"):
            dispatch(api_client, "penfield_reflect", {"time_window": "fortnight"})
        assert api.calls("POST", "/analysis/reflect") == []


class TestListArtifacts:
    @pytest.fixture
    def files(self, api: FakePenfield) -> None:
        api.artifacts.update(
            {
                "/readme.md": "root",
                "/project/notes.md": "notes",
                "/project/Meeting-2026.md": "minutes",
                "/project/meetings/q3.md": "nested",
            }
        )

    def test_lists_the_root_by_default(self, files: None, api_client: PenfieldClient) -> None:
        result = json.loads(dispatch(api_client, "penfield_list_artifacts", {}))

        assert result["folders"] == ["project"]
        assert [f["name"] for f in result["files"]] == ["readme.md"]

    def test_lists_one_directory(self, files: None, api_client: PenfieldClient) -> None:
        result = json.loads(
            dispatch(api_client, "penfield_list_artifacts", {"path_prefix": "/project/"})
        )

        assert result["folders"] == ["meetings"]
        assert [f["name"] for f in result["files"]] == ["Meeting-2026.md", "notes.md"]

    def test_filters_files_and_folders_by_name(
        self, files: None, api_client: PenfieldClient
    ) -> None:
        result = json.loads(
            dispatch(
                api_client,
                "penfield_list_artifacts",
                {"path_prefix": "/project/", "name_pattern": "meeting"},
            )
        )

        assert result["folders"] == ["meetings"]
        assert [f["name"] for f in result["files"]] == ["Meeting-2026.md"]


class TestArgumentsOfTheWrongKind:
    """A model that sends the wrong kind of value is told what the tool takes."""

    @pytest.mark.parametrize(
        ("tool", "args", "said"),
        [
            ("penfield_recall", {"query": "q", "limit": "many"}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": [3]}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": 2.5}, "limit must be a whole number"),
            ("penfield_search", {"query": "q", "limit": "ten"}, "limit must be a whole number"),
            ("penfield_list_contexts", {"limit": "x"}, "limit must be a whole number"),
            ("penfield_list_contexts", {"offset": "x"}, "offset must be a whole number"),
            (
                "penfield_restore_context",
                {"name": "n", "limit": "x"},
                "limit must be a whole number",
            ),
            ("penfield_recall", {"query": "q", "limit": True}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": "inf"}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": "-inf"}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": "nan"}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": 1e999}, "limit must be a whole number"),
            ("penfield_recall", {"query": "q", "limit": "1e999"}, "limit must be a whole number"),
            (
                "penfield_store",
                {"content": "c", "importance": "inf"},
                "importance must be a number",
            ),
            (
                "penfield_store",
                {"content": "c", "importance": float("nan")},
                "importance must be a number",
            ),
            ("penfield_recall", {"query": "q", "tags": ["a", 1]}, "tags must be a list of text"),
            ("penfield_store", {"content": "c", "tags": {"a": 1}}, "tags must be a list of text"),
            (
                "penfield_store",
                {"content": "c", "importance": "high"},
                "importance must be a number",
            ),
            (
                "penfield_reflect",
                {"include_documents": "maybe"},
                "include_documents must be true or false",
            ),
            ("penfield_store", {"content": {"k": "v"}}, "content must be text"),
            ("penfield_fetch", {"id": ["x"]}, "id must be text"),
            ("penfield_fetch", {"id": "\ud800"}, "id must be text"),
            ("penfield_save_artifact", {"path": 5, "content": "x"}, "path must be text"),
        ],
    )
    def test_the_error_names_the_argument(
        self, tool: str, args: dict[str, Any], said: str
    ) -> None:
        client = _RecordingClient()
        with pytest.raises(ValueError, match=said):
            dispatch(client, tool, args)  # type: ignore[arg-type]
        assert client.calls == [], "nothing is sent"

    @pytest.mark.parametrize("limit", [3, "3", " 3 ", 3.0])
    def test_a_number_written_as_text_is_taken_as_the_number(self, limit: object) -> None:
        client = _RecordingClient()
        dispatch(client, "penfield_recall", {"query": "q", "limit": limit})  # type: ignore[arg-type]
        sent = client.calls[0][1]["limit"]
        assert sent == 3
        assert isinstance(sent, int)

    def test_tags_written_as_one_text_are_taken_as_the_list(self) -> None:
        client = _RecordingClient()
        dispatch(client, "penfield_store", {"content": "c", "tags": "launch, dns"})  # type: ignore[arg-type]
        assert client.calls[0][1]["tags"] == ["launch", "dns"]

    @pytest.mark.parametrize(("typed", "meant"), [("true", True), ("False", False), (True, True)])
    def test_true_and_false_written_as_text_are_taken_as_such(
        self, typed: object, meant: bool
    ) -> None:
        client = _RecordingClient()
        dispatch(client, "penfield_reflect", {"include_documents": typed})  # type: ignore[arg-type]
        assert client.calls[0][1]["include_documents"] is meant

    def test_an_argument_the_tool_does_not_know_is_ignored(self) -> None:
        client = _RecordingClient()
        dispatch(client, "penfield_recall", {"query": "q", "verbose": {"x": 1}})  # type: ignore[arg-type]
        assert client.calls[0][1] == {"query": "q"}
